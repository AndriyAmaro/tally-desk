"use strict";
/*
 * Rule-based detector for prompt injection in untrusted text (news headlines, fetched web pages, chat previews).
 *
 * This is NOT a model. It is a list of regular expressions with a weight each, and it says so everywhere it speaks: a rule can
 * be evaded by rephrasing, and it cannot judge what it does not match. Precision comes before recall, because a false
 * positive blocks a decision cycle. Every rule below is narrow on purpose: it needs an imperative aimed at the reader plus a
 * target that only an attack has (previous instructions, the system prompt, a URL to send data to, a role override).
 *
 * Score: each matched rule adds its weight by noisy-or, score = 1 - product(1 - weight), rounded to 4 decimals, in [0, 1].
 * valid is false at or above THRESHOLD, true below it. The text and the findings never leave this module's return value.
 */

const PROFILE = "tally-web-injection";
const GUARDRAIL_NAME = "tally_rules";
const THRESHOLD = 0.7;
const MAX_ANALYZED = 600000; // characters; a longer text is read at its head and its tail (an attack often sits at the end)

// ---------------------------------------------------------------- hidden characters

const ZERO_WIDTH = new Set([0x200b, 0x200c, 0x200d, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xfeff, 0x180e]);
const BIDI_OVERRIDE = new Set([0x202d, 0x202e]);
const BIDI_EMBED = new Set([0x202a, 0x202b, 0x202c, 0x2066, 0x2067, 0x2068, 0x2069]);
const STRIP = /[​-‏‪-‮⁠-⁤⁦-⁩᠎﻿­\u{e0000}-\u{e007f}\u{e0100}-\u{e01ef}]/gu;

// a zero-width mark is ordinary inside emoji sequences and in scripts that use it (Arabic, Hebrew, Indic)
const JOINING_SCRIPTS = /[֐-ۿݐ-ݿࢠ-ࣿऀ-෿יִ-﷿ﹰ-﻿\u{1f000}-\u{1faff}☀-➿️]/u;
const ASCII_WORD = /[a-z0-9]/i;

function hiddenCharFindings(text) {
  const out = [];
  let zeroWidth = 0, insideWord = 0, tags = 0, variation = 0, override = 0, embed = 0;
  let prev = "", pending = []; // the last visible character, and the zero-width marks waiting for the next one
  const settle = (next) => {
    for (const mark of pending) {
      if (JOINING_SCRIPTS.test(mark.before) || JOINING_SCRIPTS.test(next)) continue;
      zeroWidth++;
      if (ASCII_WORD.test(mark.before) && ASCII_WORD.test(next)) insideWord++;
    }
    pending = [];
  };
  let first = true;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    const atStart = first;
    first = false;
    if (cp >= 0xe0000 && cp <= 0xe007f) { if (prev !== "\u{1f3f4}") tags++; continue; } // tags after a black flag are an emoji flag (England, Scotland, Wales)
    if (cp >= 0xe0100 && cp <= 0xe01ef) { variation++; continue; }
    if (BIDI_OVERRIDE.has(cp)) { override++; continue; }
    if (BIDI_EMBED.has(cp)) { embed++; continue; }
    if (ZERO_WIDTH.has(cp)) {
      if (!(cp === 0xfeff && atStart)) pending.push({ before: prev }); // a byte order mark at the start of a text is not a mark
      continue;
    }
    if (pending.length) settle(ch);
    prev = ch;
  }
  settle("");
  if (tags) out.push({ id: "hidden.tag_characters", category: "hidden_characters", weight: 0.9 });
  if (override) out.push({ id: "hidden.bidi_override", category: "hidden_characters", weight: 0.9 });
  if (variation >= 4) out.push({ id: "hidden.variation_selectors", category: "hidden_characters", weight: 0.75 });
  if (embed) out.push({ id: "hidden.bidi_embedding", category: "hidden_characters", weight: 0.35 });
  if (zeroWidth >= 10) out.push({ id: "hidden.zero_width_many", category: "hidden_characters", weight: 0.85 });
  else if (zeroWidth >= 3) out.push({ id: "hidden.zero_width_several", category: "hidden_characters", weight: 0.65 });
  else if (zeroWidth >= 1) out.push({ id: "hidden.zero_width_few", category: "hidden_characters", weight: 0.3 });
  if (insideWord) out.push({ id: "hidden.zero_width_inside_word", category: "hidden_characters", weight: 0.5 });
  return out;
}

// ---------------------------------------------------------------- phrase rules (run on the flattened, lower-cased text)

const START = "(?:^|[.!?:;,(\\[\"'>\\-]\\s*|\\b(?:please|and|then|now|also|just|first|next|kindly)\\s+)"; // an imperative opens a sentence or follows "please"

const WORD_QUAL = "(?:previous|prior|above|earlier|preceding|foregoing|original|initial|existing|system|safety|developer|former|past)";
const THINGS = "(?:instructions?|prompts?|rules|directions|directives|guidelines|commands|constraints|restrictions|programming|context|messages?|orders|training)";

const R = (id, category, weight, source, opts = {}) => ({ id, category, weight, re: new RegExp(source, "g"), negatable: !!opts.negatable });

const PHRASE_RULES = [
  // --- instruction override
  R("override.ignore_previous", "instruction_override", 0.9,
    `\\b(?:ignore|disregard|forget|override|bypass|discard|overrule|neglect|abandon)\\s+(?:all\\s+|any\\s+|every\\s+|each\\s+|both\\s+)?(?:of\\s+)?(?:the\\s+|your\\s+|my\\s+|these\\s+|those\\s+|this\\s+|that\\s+|our\\s+)?${WORD_QUAL}\\s+${THINGS}\\b`),
  R("override.ignore_your_instructions", "instruction_override", 0.9,
    "\\b(?:ignore|disregard|forget|override|bypass)\\s+(?:all\\s+|any\\s+)?(?:your|my|these|those|all|any)\\s+(?:instructions?|programming|training|directives|guidelines)\\b"),
  R("override.ignore_everything_above", "instruction_override", 0.85,
    "\\b(?:ignore|disregard|forget)\\s+(?:everything|anything|all)\\s+(?:above|before|you\\s+(?:were|have\\s+been|know)|that\\s+(?:was|came)|i\\s+(?:said|told)|(?:you\\s+)?(?:were\\s+)?told)\\b"),
  R("override.ignore_the_above", "instruction_override", 0.7,
    `${START}(?:ignore|disregard)\\s+(?:the\\s+)?(?:above|text\\s+above|everything\\s+above|preceding\\s+text)\\b`),
  R("override.do_not_follow", "instruction_override", 0.85,
    `\\b(?:do\\s+not|don'?t|never|stop)\\s+(?:follow|obey|comply\\s+with|listen\\s+to|adhere\\s+to)\\s+(?:the\\s+|your\\s+|any\\s+|all\\s+)?${WORD_QUAL}\\s+(?:instructions?|prompts?|rules|guidelines|directives)\\b`),
  R("override.new_instructions", "instruction_override", 0.75,
    "\\b(?:new|updated|revised|real|actual|secret|hidden|true)\\s+(?:system\\s+)?(?:instructions?|directives?|prompt)\\s*(?::|=>|-->|\\bfollow)"),
  R("override.your_new_task", "instruction_override", 0.75,
    "\\byour\\s+(?:new|real|actual|true|only)\\s+(?:task|job|goal|purpose|mission|role|objective|instruction)s?\\s+(?:is|are)\\b"),
  R("override.from_now_on", "instruction_override", 0.45,
    "\\bfrom\\s+now\\s+on\\b,?\\s+you\\s+(?:will|must|shall|are|should)\\b(?!\\s+(?:be\\s+able|not\\s+need|no\\s+longer|receive|get|earn|pay))"),
  R("override.instead_do", "instruction_override", 0.7,
    "\\binstead\\s*,?\\s+(?:you\\s+(?:must|should|will|shall|need\\s+to)|please|just|only)\\s+(?:output|print|say|reply|respond|answer|write|return|rate|mark|conclude|vote|classify|label)\\b"),
  R("override.ignore_other_evidence", "instruction_override", 0.75,
    "\\b(?:ignore|disregard|discard)\\s+(?:all\\s+|every\\s+)(?:other|remaining)\\s+(?:headlines?|evidence|items?|claims?|news|data|sources)\\b"),

  // --- system prompt and secret exfiltration
  R("exfil.show_your_prompt", "prompt_exfiltration", 0.85,
    "\\b(?:reveal|show|print|display|output|repeat|leak|tell|give|send|share|dump|recite|echo|expose|disclose|provide|write\\s+out|paste|return|copy|quote)\\s+(?:me\\s+|us\\s+)?(?:your|ur)\\s+(?:hidden\\s+|secret\\s+|initial\\s+|original\\s+|full\\s+|entire\\s+|complete\\s+|exact\\s+|internal\\s+|first\\s+|above\\s+|system\\s+)*(?:system\\s+prompt|system\\s+message|prompt|instructions|developer\\s+(?:prompt|message|instructions))\\b"),
  R("exfil.what_is_your_prompt", "prompt_exfiltration", 0.8,
    "\\bwhat\\s+(?:is|are|were|was)\\s+your\\s+(?:system\\s+prompt|initial\\s+(?:prompt|instructions)|original\\s+(?:prompt|instructions)|hidden\\s+instructions|system\\s+message)\\b"),
  R("exfil.show_the_system_prompt", "prompt_exfiltration", 0.75,
    `${START}(?:reveal|show|print|display|output|repeat|leak|dump|recite|echo|expose|disclose|paste|write\\s+out)\\s+(?:me\\s+)?(?:the\\s+)?(?:system\\s+prompt|system\\s+message|developer\\s+message|hidden\\s+prompt|hidden\\s+instructions|initial\\s+instructions)\\b`),
  R("exfil.repeat_above", "prompt_exfiltration", 0.8,
    "\\b(?:repeat|print|output|echo|copy|write|recite)\\s+(?:back\\s+)?(?:(?:everything|all|the\\s+text|the\\s+content|this\\s+conversation|the\\s+conversation)\\s+(?:above|before|so\\s+far|up\\s+to\\s+(?:here|this\\s+point)|from\\s+the\\s+(?:start|beginning))|the\\s+words\\s+above)\\b"),
  R("exfil.secrets", "prompt_exfiltration", 0.85,
    "\\b(?:reveal|show|print|display|output|leak|send|share|give|tell|provide|expose|dump|post|paste|email|forward|upload)\\s+(?:me\\s+|us\\s+)?(?:your|ur|the\\s+user'?s|all\\s+(?:of\\s+)?(?:your|the))\\s+(?:api\\s*[ _-]?keys?|secrets?|credentials?|passwords?|access\\s+tokens?|auth(?:orization)?\\s+tokens?|bearer\\s+tokens?|private\\s+keys?|seed\\s+phrases?|recovery\\s+phrases?|mnemonic|environment\\s+variables?|env\\s+vars?|\\.env|session\\s+cookies?)\\b",
    { negatable: true }),

  // --- send data to a URL
  R("send.data_to_url", "send_to_url", 0.85,
    "\\b(?:send|post|forward|upload|transmit|exfiltrate|leak|submit|email|e-mail|append|encode|attach|paste|write|report|stream|include|put|add|pass|embed)\\b[^.\\n!?]{0,100}?\\b(?:system\\s+prompt|instructions|conversation|chat\\s+history|api\\s*[ _-]?keys?|secrets?|credentials|passwords?|private\\s+keys?|seed\\s+phrase|the\\s+prompt|your\\s+(?:response|answer|output|reply|analysis)|everything|all\\s+(?:of\\s+)?(?:the\\s+)?(?:above|text|data|information))\\b[^.\\n!?]{0,60}?\\b(?:to|at|into|via|in|through|using)\\b[^\\n]{0,30}?https?://",
    { negatable: true }),
  R("send.http_request", "send_to_url", 0.8,
    "\\b(?:make|issue|perform|send|submit|fire)\\s+(?:an?\\s+)?(?:http[s]?\\s+|get\\s+|post\\s+|web\\s+|network\\s+|api\\s+)(?:request|call)\\s+(?:to|at)\\s+https?://"),
  R("send.pipe_to_shell", "send_to_url", 0.85,
    "\\b(?:curl|wget)\\b[^\\n]{0,200}\\|\\s*(?:sudo\\s+)?(?:ba|z)?sh\\b"),
  R("send.curl_url", "send_to_url", 0.5,
    "\\b(?:curl|wget)\\s+(?:-[a-z-]+\\s+)*[\"']?https?://"),
  R("send.markdown_placeholder", "send_to_url", 0.8,
    "\\]\\(\\s*https?://[^)\\s]*(?:[=/]\\{[a-z_ ]{1,40}\\}|=<[a-z_ ]{1,40}>|=\\[[a-z_ ]{1,40}\\]|%7b[a-z_]{1,40}%7d)"),
  R("send.visit_and_obey", "send_to_url", 0.75,
    "\\b(?:visit|open|browse|navigate\\s+to|go\\s+to|fetch|load|access)\\s+(?:the\\s+)?(?:url\\s+|link\\s+|page\\s+|site\\s+|website\\s+)?(?:at\\s+)?https?://\\S{1,200}\\s*(?:,|and|then)?\\s*(?:and\\s+)?(?:then\\s+)?(?:follow\\s+(?:the\\s+|its\\s+|those\\s+|these\\s+|all\\s+)?(?:instructions|directions|commands|orders)|obey|execute|carry\\s+out)"),
  R("send.decode_and_run", "send_to_url", 0.8,
    "\\b(?:(?:decode|decrypt|deobfuscate)\\s+(?:and\\s+|then\\s+)?(?:execute|run|follow|obey|eval)|(?:execute|run|eval|follow|obey)\\s+(?:the\\s+)?(?:following\\s+|this\\s+)?(?:base64|hex|rot13|encoded|obfuscated))\\b"),

  // --- role and format hijack
  R("role.you_are_now", "role_hijack", 0.85,
    "\\byou\\s+are\\s+now\\s+(?:an?\\s+|the\\s+|in\\s+)?(?:dan\\b|unrestricted|unfiltered|uncensored|jailbroken|evil|developer\\s+mode|god\\s+mode|admin\\b|root\\b|sudo|free\\s+of|no\\s+longer|a\\s+different|a\\s+new\\s+ai|an\\s+ai\\s+(?:with|without|that))"),
  R("role.act_as_unrestricted", "role_hijack", 0.85,
    "\\b(?:act|behave|respond|answer|reply|operate|function)\\s+(?:as|like)\\s+(?:if\\s+you\\s+(?:were|are)\\s+)?(?:an?\\s+|the\\s+)?(?:unrestricted|unfiltered|uncensored|jailbroken|evil|dan\\b|developer\\s+mode|system\\s+administrator|root\\s+user)"),
  R("role.pretend", "role_hijack", 0.8,
    "\\bpretend\\s+(?:to\\s+be|you\\s+are|that\\s+you\\s+are)\\s+(?:an?\\s+)?(?:unrestricted|unfiltered|uncensored|jailbroken|evil|dan\\b|different\\s+ai|the\\s+(?:system|developer|admin))"),
  R("role.jailbreak_mode", "role_hijack", 0.85,
    "\\b(?:do\\s+anything\\s+now|jailbreak(?:ed|en)?\\s+mode|developer\\s+mode\\s+(?:enabled|on|activated)|dan\\s+mode|sudo\\s+mode|ignore\\s+(?:all\\s+)?safety\\s+(?:rules|guidelines|filters?|policies))\\b"),
  R("role.debug_mode", "role_hijack", 0.6,
    "\\b(?:debug|god|admin|maintenance)\\s+mode\\s+(?:enabled|on|activated|unlocked)\\b"),
  R("role.chat_template_tokens", "format_hijack", 0.9,
    "<\\|[a-z_ ]{2,30}\\|>|\\[/?inst\\]|<<\\/?sys>>|\\[/?system\\]"),
  R("format.spoofed_delimiter", "format_hijack", 0.6,
    "</?(?:untrusted|evidence|headlines?|system|instructions?|prompt|assistant|search_results|tool_result|function_results?)>"),
  R("format.end_of_untrusted", "format_hijack", 0.75,
    "\\bend\\s+of\\s+(?:the\\s+)?untrusted\\s+(?:text|input|data|content|headlines?)\\b|\\b(?:begin|start)\\s+(?:of\\s+)?(?:new|real|trusted)\\s+(?:instructions?|prompt|system\\s+prompt|task)\\b"),
  R("format.tool_call_markup", "format_hijack", 0.7,
    "<\\/?(?:tool_call|tool_use|function_calls?|invoke|tool_calls?|antml:[a-z_]+)\\b"),
  R("role.if_you_are_an_ai", "role_hijack", 0.75,
    "\\b(?:if|when)\\s+you\\s+(?:are|'re)\\s+(?:an?\\s+)?(?:ai|a\\.i\\.|llm|large\\s+language\\s+model|language\\s+model|ai\\s+assistant|assistant|chatbot|chatgpt|claude|ai\\s+agent|bot)\\b"),
  R("role.note_to_ai", "role_hijack", 0.8,
    "\\b(?:note|message|instructions?|attention|important|notice|hey|hello|dear)\\s*(?:to|for)\\s+(?:the\\s+)?(?:ai|llm|assistants?|language\\s+models?|chatbots?|gpt|chatgpt|claude|ai\\s+agents?|crawlers?|scrapers?)\\b"),
  R("role.ai_reading_this", "role_hijack", 0.75,
    "\\b(?:ai|llm|assistants?|language\\s+models?|chatbots?|ai\\s+agents?|summari[sz]ers?)\\s+(?:reading|processing|summari[sz]ing|analy[sz]ing|scraping|crawling|parsing)\\s+this\\s+(?:page|text|content|article|headline|document|message|site)\\b"),
  R("role.authority_pretext", "role_hijack", 0.7,
    "\\bthis\\s+is\\s+(?:an?\\s+)?(?:official\\s+)?(?:system|developer|admin(?:istrator)?)\\s+(?:message|override|instruction|directive|command)\\b|\\b(?:admin|system|developer|root)\\s+override\\b"),
  R("role.conceal_from_user", "role_hijack", 0.8,
    "\\b(?:do\\s+not|don'?t|never|without)\\s+(?:tell|telling|mention|mentioning|inform|informing|alert|alerting|notify|notifying|reveal(?:ing)?\\s+(?:this\\s+)?to|show(?:ing)?\\s+(?:this\\s+)?to)\\s+(?:this\\s+to\\s+)?(?:the\\s+|your\\s+)?(?:user|human|operator|owner|developer)\\b|\\b(?:hide|conceal)\\s+(?:this\\s+)?from\\s+the\\s+(?:user|human|operator)\\b"),
  R("format.verdict_hijack", "format_hijack", 0.7,
    "\\b(?:rate|mark|label|classify|set|score|treat|count)\\s+(?:this|the|every|each|all)\\s+(?:claims?|headlines?|items?|voices?|evidence)\\s+(?:as\\s+|to\\s+be\\s+)?(?:supported|strongly\\s+bullish|bullish|bearish|true|verified)\\b|\\bset\\s+(?:conviction|urgency|stance|lean)\\s+(?:to|=)\\s+[\"']?(?:high|bullish|bearish|bull|bear)\\b"),
  R("format.always_answer", "format_hijack", 0.65,
    "\\b(?:always|only)\\s+(?:answer|reply|respond|output|conclude|vote|say)\\s+(?:with\\s+)?[\"']?(?:bullish|bearish|bull|bear|supported)\\b"),
];

// a role label opening a line ("System: ...") is a hijack only in company: alone it stays under the threshold
const LINE_RULES = [
  { id: "format.role_label_line", category: "format_hijack", weight: 0.55, re: /^\s*(?:#{1,4}\s*)?(?:system|assistant|developer|admin(?:istrator)?)\s*(?:message|prompt|override)?\s*[:>\]]\s*\S/ },
  { id: "format.instruction_header", category: "format_hijack", weight: 0.7, re: /^\s*#{2,}\s*(?:system|new\s+instructions?|instructions?\s+for\s+(?:the\s+)?(?:ai|assistant|model)|assistant|developer)\s*:?\s*$/ },
];

const NEGATION = /(?:\b(?:never|don'?t|dont|do\s+not|does\s+not|doesn'?t|should\s+not|shouldn'?t|must\s+not|mustn'?t|cannot|can'?t|won'?t|will\s+not|avoid|beware|warn\w*|scam\w*|phish\w*|fake|fraud\w*|ask(?:s|ed|ing)?|trick\w*|lure\w*|urge\w*|not\s+to|without)\b)/;

function negatedBefore(flat, index) {
  return NEGATION.test(flat.slice(Math.max(0, index - 60), index));
}

// ---------------------------------------------------------------- normalisation

function flatten(text) {
  return text
    .replace(STRIP, "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[‘’‚‛′]/g, "'")
    .replace(/[“”„‟″]/g, '"')
    .replace(/[‐-―−]/g, "-")
    .replace(/[*`~]/g, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function clip(text) {
  return text.length <= MAX_ANALYZED ? text : text.slice(0, MAX_ANALYZED / 2) + "\n" + text.slice(-MAX_ANALYZED / 2);
}

function firstHit(rule, flat) {
  rule.re.lastIndex = 0;
  for (let n = 0, m; n < 25 && (m = rule.re.exec(flat)) !== null; n++) {
    if (m[0].length === 0) { rule.re.lastIndex++; continue; }
    if (!rule.negatable || !negatedBefore(flat, m.index)) return m;
  }
  return null;
}

// ---------------------------------------------------------------- the detector

function analyze(input) {
  const raw = clip(String(input == null ? "" : input));
  const findings = hiddenCharFindings(raw);
  const flat = flatten(raw);
  for (const rule of PHRASE_RULES) {
    if (firstHit(rule, flat)) findings.push({ id: rule.id, category: rule.category, weight: rule.weight });
  }
  const lines = raw.split(/\r?\n/).map((l) => l.replace(STRIP, "").normalize("NFKC").toLowerCase().slice(0, 400));
  for (const rule of LINE_RULES) {
    if (lines.some((l) => rule.re.test(l))) findings.push({ id: rule.id, category: rule.category, weight: rule.weight });
  }
  let rest = 1;
  for (const f of findings) rest *= 1 - f.weight;
  const score = Math.round((1 - rest) * 10000) / 10000;
  return { score, findings };
}

/** The answer of POST /validate for a text: { valid, explanation: null, score }. */
function validate(input) {
  const { score } = analyze(input);
  return { valid: score < THRESHOLD, explanation: null, score };
}

module.exports = { PROFILE, GUARDRAIL_NAME, THRESHOLD, MAX_ANALYZED, analyze, validate, flatten, RULE_IDS: [...PHRASE_RULES, ...LINE_RULES].map((r) => r.id) };
