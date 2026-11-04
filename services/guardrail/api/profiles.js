"use strict";
const { adapt } = require("../lib/vercel");
const { profiles } = require("../lib/service");

module.exports = adapt((request) => profiles(request));
