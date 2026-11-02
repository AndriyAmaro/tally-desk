# Legacy: the self-hosted gateway of the earlier project

`gate.py` is the small LLM gateway the project ran before it moved to Otari: it tried free models in the order of
[`config/model_routing.json`](../../config/model_routing.json) (a ranking computed from the ledger; the models listed there, Gemini among
them, are the ones that answered then). **The Otari path does not use either file.** In Otari mode every call goes to the hosted gateway and
the roles come from [`config/otari.json`](../../config/otari.json).

They stay in the tree because many of the first 791 dossiers of the ledger were recorded through this gateway, and the tests that pin those
dossiers (`test_gateway`, `test_models`, `test_route_log`) read both files.
