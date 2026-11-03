import { readFileSync, writeFileSync, readdirSync, unlinkSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
const html = readFileSync(
  new URL("../../web/console/index.html", import.meta.url),
  "utf8",
).replace(/\r+\n/g, "\n");
writeFileSync(new URL("../../web/console/index.html", import.meta.url), html);
writeFileSync(
  new URL("../../web/club-preview.html", import.meta.url),
  html.replaceAll("./assets/", "./console/assets/"),
);
const assets = fileURLToPath(
  new URL("../../web/console/assets/", import.meta.url),
);
const keep = new Set(
  [...html.matchAll(/\.\/assets\/([^"\s]+)/g)].map((m) => m[1]),
);
for (const name of keep) {
  if (!name.endsWith(".js")) continue;
  const code = readFileSync(path.join(assets, name), "utf8");
  for (const m of code.matchAll(/["']\.\/([^"']+\.(?:js|css))["']/g))
    keep.add(m[1]);
}
for (const name of readdirSync(assets)) {
  const target = path.resolve(assets, name);
  if (path.dirname(target) !== path.resolve(assets))
    throw new Error("Invalid asset path");
  if (/^[\w-]+\.(?:js|css)$/.test(name) && !keep.has(name)) unlinkSync(target);
}
