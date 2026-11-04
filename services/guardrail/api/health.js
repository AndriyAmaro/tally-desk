"use strict";
const { adapt } = require("../lib/vercel");
const { health } = require("../lib/service");

module.exports = adapt((request) => health(request));
