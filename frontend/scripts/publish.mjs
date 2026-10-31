import { readFileSync, writeFileSync } from "node:fs";
const html = readFileSync(
  new URL("../../web/console/index.html", import.meta.url),
  "utf8",
);
writeFileSync(
  new URL("../../web/club-preview.html", import.meta.url),
  html.replaceAll("./assets/", "./console/assets/"),
);
