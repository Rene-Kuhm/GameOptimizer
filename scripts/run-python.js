const { existsSync, readdirSync } = require("node:fs");
const { join, dirname, basename } = require("node:path");
const { spawnSync } = require("node:child_process");

function pythonCandidates() {
  const winVenv = join(process.cwd(), ".venv", "Scripts", "python.exe");
  const posixVenv = join(process.cwd(), ".venv", "bin", "python");
  return [winVenv, posixVenv, "python3", "python"];
}

function commandExists(command) {
  if (command.includes("/") || command.includes("\\")) {
    return existsSync(command);
  }
  const result = spawnSync(command, ["--version"], { stdio: "ignore" });
  return result.status === 0;
}

function expandGlobArg(arg) {
  if (!arg.includes("*")) return [arg];
  const dir = dirname(arg);
  const pattern = basename(arg).replace(/\./g, "\\.").replace(/\*/g, ".*");
  const regex = new RegExp(`^${pattern}$`);
  if (!existsSync(dir)) return [arg];
  return readdirSync(dir)
    .filter((entry) => regex.test(entry))
    .map((entry) => join(dir, entry));
}

const python = pythonCandidates().find(commandExists);
if (!python) {
  console.error("No Python interpreter found. Install python3 or create .venv.");
  process.exit(1);
}

const args = process.argv.slice(2).flatMap(expandGlobArg);
const result = spawnSync(python, args, { stdio: "inherit" });
process.exit(result.status ?? 1);
