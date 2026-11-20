// Builds src/basemap.json: country borders around Belgrade from Natural Earth (world-atlas, 1:10m),
// used as an offline background when map tiles cannot load.
import { readFileSync, writeFileSync } from "node:fs";
import { feature } from "topojson-client";

const topo = JSON.parse(readFileSync(new URL("../node_modules/world-atlas/countries-10m.json", import.meta.url)));
const all = feature(topo, topo.objects.countries);
const keep = new Set(["Serbia", "Hungary", "Romania", "Croatia", "Bosnia and Herz.", "Bulgaria", "Montenegro", "Kosovo", "Macedonia", "North Macedonia"]);
const round = (c) => (typeof c[0] === "number" ? [Math.round(c[0] * 1e4) / 1e4, Math.round(c[1] * 1e4) / 1e4] : c.map(round));
const features = all.features
  .filter((f) => keep.has(f.properties.name))
  .map((f) => ({ type: "Feature", properties: { name: f.properties.name }, geometry: { type: f.geometry.type, coordinates: round(f.geometry.coordinates) } }));
writeFileSync(new URL("../src/basemap.json", import.meta.url), JSON.stringify({ type: "FeatureCollection", features }));
console.log("countries:", features.map((f) => f.properties.name).join(", "));
