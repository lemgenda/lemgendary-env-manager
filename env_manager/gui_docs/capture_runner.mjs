import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const puppeteer = require("puppeteer");

function parseArgs(argv) {
  const args = {
    url: "http://localhost:1420",
    outDir: "",
    specsJson: "",
    auditOut: "",
    pruneTestPngs: true,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    const val = argv[i + 1];
    if (key === "--url" && val) {
      args.url = val;
      i += 1;
    } else if (key === "--out-dir" && val) {
      args.outDir = val;
      i += 1;
    } else if (key === "--specs-json" && val) {
      args.specsJson = val;
      i += 1;
    } else if (key === "--audit-out" && val) {
      args.auditOut = val;
      i += 1;
    } else if (key === "--no-prune") {
      args.pruneTestPngs = false;
    }
  }
  return args;
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function closeAnyOpenModal(page) {
  await page.keyboard.press("Escape");
  await delay(150);
  await page.evaluate(() => {
    const closeBtn = document.querySelector(
      "[role='dialog'] button[aria-label*='Close'], #test-modal-btn-close"
    );
    if (closeBtn instanceof HTMLElement) {
      closeBtn.click();
    }
  });
  await delay(150);
}

async function runSetupSteps(page, steps) {
  for (const step of steps || []) {
    if (step.action === "click_text" && step.text) {
      const needle = step.text.toLowerCase();
      await page.evaluate((sel, text) => {
        const nodes = Array.from(document.querySelectorAll(sel));
        const match = nodes.find((n) => (n.textContent || "").toLowerCase().includes(text));
        if (match instanceof HTMLElement) {
          match.scrollIntoView({ block: "center", inline: "nearest" });
          match.click();
        }
      }, step.selector, needle);
      await delay(350);
      continue;
    }
    const el = await page.$(step.selector);
    if (!el) {
      continue;
    }
    await el.evaluate((node) => node.scrollIntoView({ block: "center", inline: "nearest" }));
    if (step.action === "click") {
      await el.evaluate((node) => node.click());
      await delay(350);
    } else if (step.action === "hover") {
      await el.hover();
      await delay(300);
    } else if (step.action === "focus") {
      await el.focus();
      await delay(300);
    }
  }
}

async function injectCalloutBadges(page, callouts, clip) {
  const result = await page.evaluate((items, clipBox) => {
    const existing = document.getElementById("lem-doc-callout-overlay");
    if (existing) {
      existing.remove();
    }
    const overlay = document.createElement("div");
    overlay.id = "lem-doc-callout-overlay";
    overlay.style.position = "fixed";
    overlay.style.inset = "0";
    overlay.style.pointerEvents = "none";
    overlay.style.zIndex = "99999";

    const placed = [];
    const missing = [];
    const badgeSize = 24;
    const minX = clipBox ? Math.max(4, clipBox.x - window.scrollX + 4) : 4;
    const minY = clipBox ? Math.max(4, clipBox.y - window.scrollY + 4) : 4;
    const maxX = (clipBox ? clipBox.x - window.scrollX + clipBox.width : window.innerWidth) - badgeSize - 4;
    const maxY = (clipBox ? clipBox.y - window.scrollY + clipBox.height : window.innerHeight) - badgeSize - 4;

    for (const item of items) {
      const target = document.querySelector(item.selector);
      if (!target) {
        missing.push(item.selector);
        continue;
      }
      const rect = target.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) {
        missing.push(`${item.selector} (0x0)`);
        continue;
      }
      let x = Math.max(minX, Math.min(maxX, rect.left - 8));
      let y = Math.max(minY, Math.min(maxY, rect.top - 8));

      for (let attempt = 0; attempt < 16; attempt += 1) {
        const collides = placed.some(
          (p) => Math.abs(p.x - x) < badgeSize + 2 && Math.abs(p.y - y) < badgeSize + 2
        );
        if (!collides) {
          break;
        }
        x = Math.min(maxX, x + badgeSize + 4);
        if (x >= maxX - 4) {
          x = Math.max(minX, rect.left);
          y = Math.min(maxY, y + badgeSize + 4);
        }
      }
      placed.push({ number: item.number, x, y });

      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("width", String(badgeSize));
      svg.setAttribute("height", String(badgeSize));
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.style.position = "fixed";
      svg.style.left = `${x}px`;
      svg.style.top = `${y}px`;
      svg.style.filter = "drop-shadow(0 2px 4px rgba(0,0,0,0.45))";

      const circle = document.createElementNS(svgNS, "circle");
      circle.setAttribute("cx", "12");
      circle.setAttribute("cy", "12");
      circle.setAttribute("r", "11");
      circle.setAttribute("fill", "#2563eb");
      circle.setAttribute("stroke", "#ffffff");
      circle.setAttribute("stroke-width", "2");

      const text = document.createElementNS(svgNS, "text");
      text.setAttribute("x", "12");
      text.setAttribute("y", "16");
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("fill", "#ffffff");
      text.setAttribute("font-family", "Inter, Arial, sans-serif");
      text.setAttribute("font-size", "12");
      text.setAttribute("font-weight", "700");
      text.textContent = String(item.number);

      svg.appendChild(circle);
      svg.appendChild(text);
      overlay.appendChild(svg);
    }

    document.body.appendChild(overlay);
    return { count: placed.length, missing };
  }, callouts, clip);
  if (result.missing.length > 0) {
    throw new Error(`Missing callout selectors: ${result.missing.join(", ")}`);
  }
  return result.count;
}

async function clearCalloutBadges(page) {
  await page.evaluate(() => {
    const existing = document.getElementById("lem-doc-callout-overlay");
    if (existing) {
      existing.remove();
    }
  });
}

async function getClipBox(page, clipSelector) {
  if (!clipSelector) {
    return null;
  }
  const neededHeight = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.ceil(r.height + 240);
  }, clipSelector);
  const currentVp = page.viewport();
  if (neededHeight > (currentVp ? currentVp.height : 1100)) {
    await page.setViewport({ width: 1440, height: Math.min(2800, neededHeight), deviceScaleFactor: 1.5 });
    await delay(150);
  }
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) {
      return null;
    }
    const isModal = Boolean(el.closest(".modal-backdrop, [role='dialog']"));
    if (!isModal) {
      el.scrollIntoView({ block: "start", inline: "nearest" });
    }
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) {
      return null;
    }
    const pad = 14;
    const x = Math.max(0, r.left - pad + window.scrollX);
    const y = Math.max(0, r.top - pad + window.scrollY);
    const width = Math.min(window.innerWidth - Math.max(0, r.left - pad), r.width + pad * 2);
    const height = Math.min(window.innerHeight - Math.max(0, r.top - pad), r.height + pad * 2);
    if (width < 40 || height < 40) {
      return null;
    }
    return { x, y, width, height };
  }, clipSelector);
}

async function collectLiveAudit(page, tabIds) {
  const audit = { url: page.url(), tabs: {} };
  for (const tabId of tabIds) {
    await closeAnyOpenModal(page);
    const tabBtn = await page.$(`#${tabId}`);
    if (tabBtn) {
      await tabBtn.click();
      await delay(350);
    }
    audit.tabs[tabId] = await page.evaluate(() => {
      const ids = Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
      const unIdControls = Array.from(
        document.querySelectorAll("button, a, input, select, textarea, [role='button'], [role='tab']")
      )
        .filter((el) => !el.id)
        .map((el) => ({
          tag: el.tagName.toLowerCase(),
          label: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 80),
        }));
      return { ids, un_id_controls: unIdControls };
    });
  }
  return audit;
}

function pruneObsoleteTestPngs(outDir) {
  const entries = fs.readdirSync(outDir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isFile() && /^test_.*\.png$/i.test(entry.name)) {
      fs.unlinkSync(path.join(outDir, entry.name));
    }
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.outDir || !args.specsJson) {
    throw new Error("Both --out-dir and --specs-json are required.");
  }
  fs.mkdirSync(args.outDir, { recursive: true });
  const specs = JSON.parse(fs.readFileSync(args.specsJson, "utf-8"));

  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 1100, deviceScaleFactor: 1.5 });
    await page.goto(args.url, { waitUntil: "networkidle2", timeout: 30000 });
    await page.waitForSelector("#tab-dashboard", { timeout: 15000 });
    await page.waitForSelector("#hw-card-title", { timeout: 20000 }).catch(() => {});
    await page.waitForSelector("#proj-title-lemgendary-env-manager", { timeout: 20000 }).catch(() => {});
    await delay(800);

    const results = [];
    for (const spec of specs) {
      const [baseW, baseH] = spec.viewport || [1440, 1100];
      await page.setViewport({ width: baseW, height: baseH, deviceScaleFactor: 1.5 });
      await closeAnyOpenModal(page);
      await clearCalloutBadges(page);
      const tabBtn = await page.$(`#${spec.tab_id}`);
      if (tabBtn) {
        await tabBtn.click();
        await delay(350);
        if (spec.tab_id === "tab-datasets") {
          await page.waitForSelector("#comp-select-manifold option:nth-child(2)", { timeout: 20000 }).catch(() => {});
          await page.waitForSelector(".manifolds-masonry-container .card-compact", { timeout: 20000 }).catch(() => {});
          await delay(400);
        } else if (spec.tab_id === "tab-training") {
          await page.waitForSelector(".card-compact-toggle", { timeout: 15000 }).catch(() => {});
          await delay(350);
        } else if (spec.tab_id === "tab-health") {
          await page.waitForSelector("#drift-matrix-title", { timeout: 15000 }).catch(() => {});
          await delay(350);
        } else if (spec.tab_id === "tab-docs") {
          await page.waitForSelector("iframe[title*='Documentation']", { timeout: 15000 }).catch(() => {});
          await delay(500);
        }
      }
      await runSetupSteps(page, spec.setup_steps);
      await delay(250);
      const keepFocusOrHover = (spec.setup_steps || []).some(
        (s) => s.action === "hover" || s.action === "focus"
      );
      if (!keepFocusOrHover) {
        await page.evaluate(() => {
          if (document.activeElement instanceof HTMLElement) {
            document.activeElement.blur();
          }
        });
        await page.mouse.move(0, 0);
        await delay(150);
      }

      const clip = await getClipBox(page, spec.clip_selector);
      if (!clip) {
        await page.evaluate(() => {
          window.scrollTo(0, 0);
          document.documentElement.scrollTop = 0;
          document.body.scrollTop = 0;
          for (const el of document.querySelectorAll("main, .main-content, .workspace-main, .panel-container")) {
            el.scrollTop = 0;
          }
        });
        await delay(100);
        const maxBottom = await page.evaluate((items) => {
          let bottom = 0;
          for (const item of items) {
            const el = document.querySelector(item.selector);
            if (el) {
              bottom = Math.max(bottom, el.getBoundingClientRect().bottom);
            }
          }
          return bottom;
        }, spec.callouts);
        if (maxBottom + 80 > baseH) {
          await page.setViewport({
            width: baseW,
            height: Math.min(2800, Math.ceil(maxBottom + 80)),
            deviceScaleFactor: 1.5,
          });
          await delay(150);
        }
      }
      const placedCount = await injectCalloutBadges(page, spec.callouts, clip);
      await delay(100);

      const outPath = path.join(args.outDir, spec.filename);
      await page.screenshot({
        path: outPath,
        ...(clip ? { clip } : {}),
      });
      await clearCalloutBadges(page);
      results.push({ filename: spec.filename, calloutsPlaced: placedCount });
    }

    if (args.auditOut) {
      const tabIds = [...new Set(specs.map((s) => s.tab_id))];
      const audit = await collectLiveAudit(page, tabIds);
      fs.writeFileSync(args.auditOut, JSON.stringify(audit, null, 2), "utf-8");
    }

    if (args.pruneTestPngs) {
      pruneObsoleteTestPngs(args.outDir);
    }

    process.stdout.write(JSON.stringify({ captured: results.length, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  process.stderr.write(`${err.stack || err.message}\n`);
  process.exit(1);
});
