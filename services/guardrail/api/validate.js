"use strict";
const { adapt } = require("../lib/vercel");
const { validateRequest } = require("../lib/service");

module.exports = adapt(validateRequest);
