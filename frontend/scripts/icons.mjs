// Rasterise public/icons/icon.svg into the home-screen icons and the iPad/iPhone launch
// screens (apple-touch-startup-image, light + dark). Re-run after changing the icon or the
// background tokens: node scripts/icons.mjs
import { chromium } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";

const svg = readFileSync("public/icons/icon.svg", "utf8");
const uri = `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
const font = readFileSync("node_modules/@fontsource-variable/archivo/files/archivo-latin-wght-normal.woff2").toString("base64");
const FACE = `<style>@font-face{font-family:Archivo;font-weight:100 900;src:url(data:font/woff2;base64,${font}) format("woff2")}</style>`;
const THEMES = { light: { bg: "#ffffff", ink: "#111111" }, dark: { bg: "#000000", ink: "#ffffff" } };

// [css width, css height, device pixel ratio] in portrait; landscape is generated too.
export const SPLASH = [
  [1032, 1376, 2], // iPad Pro 13" (M4)
  [1024, 1366, 2], // iPad Pro 12.9"
  [834, 1210, 2], // iPad Pro 11" (M4)
  [834, 1194, 2], // iPad Pro 11"
  [820, 1180, 2], // iPad Air 10.9" / iPad 10th
  [834, 1112, 2], // iPad Air 10.5"
  [810, 1080, 2], // iPad 10.2"
  [768, 1024, 2], // iPad mini 5 / 9.7"
  [744, 1133, 2], // iPad mini 6
  [430, 932, 3], // iPhone Pro Max
  [393, 852, 3], // iPhone Pro
  [390, 844, 3], // iPhone
];

const browser = await chromium.launch();
const page = await browser.newPage();
mkdirSync("public/icons", { recursive: true });
mkdirSync("public/splash", { recursive: true });

async function icon(file, size, { pad = 0, bg = "transparent", radius = true } = {}) {
  await page.setViewportSize({ width: size, height: size });
  const inner = size - pad * 2;
  // Apple and maskable icons are full-bleed squares: the OS applies its own mask.
  const clip = radius ? "" : "clip-path: inset(0);";
  await page.setContent(`<html><body style="margin:0;background:${bg};width:${size}px;height:${size}px;display:grid;place-items:center">
    <img src="${uri}" style="width:${inner}px;height:${inner}px;${clip}"></body></html>`);
  await page.screenshot({ path: `public/icons/${file}`, omitBackground: bg === "transparent" });
}

await icon("icon-192.png", 192);
await icon("icon-512.png", 512);
// Full-bleed variants: no transparent corners (iOS would fill them black).
for (const [file, size] of [["apple-touch-icon.png", 180], ["apple-touch-icon-167.png", 167], ["apple-touch-icon-152.png", 152]]) {
  await icon(file, size, { bg: "#111111", pad: -Math.round(size * 0.06) });
}
await icon("icon-maskable-512.png", 512, { bg: "#111111", pad: 56 });

const links = [];
for (const [w, h, dpr] of SPLASH) {
  for (const [orient, cw, ch] of [["portrait", w, h], ["landscape", h, w]]) {
    for (const [theme, { bg, ink }] of Object.entries(THEMES)) {
      const name = `splash-${cw}x${ch}@${dpr}x-${theme}.png`;
      await page.setViewportSize({ width: cw * dpr, height: ch * dpr });
      const s = Math.round(Math.min(cw, ch) * 0.22) * dpr;
      await page.setContent(`<html>${FACE}<body style="margin:0;background:${bg};height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:${s * 0.18}px;font:700 ${s * 0.26}px/1 Archivo,sans-serif;color:${ink};letter-spacing:-0.02em">
        <img src="${uri}" style="width:${s}px;height:${s}px">cookt</body></html>`);
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `public/splash/${name}` });
      links.push(
        `<link rel="apple-touch-startup-image" media="screen and (device-width: ${w}px) and (device-height: ${h}px) and (-webkit-device-pixel-ratio: ${dpr}) and (orientation: ${orient}) and (prefers-color-scheme: ${theme})" href="/splash/${name}" />`,
      );
    }
  }
}
await browser.close();
console.log(links.join("\n"));
