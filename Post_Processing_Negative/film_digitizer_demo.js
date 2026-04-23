const pptxgen = require("pptxgenjs");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.title = "Film Digitization System — ECE 445 Demo";
pres.author = "ECE 445";

// ─── Palette ─────────────────────────────────────────────────────────────────
const C = {
  bg:      "1A1F2E",
  card:    "242B3D",
  card2:   "2D3550",
  teal:    "00B4D8",
  tealDk:  "0077A8",
  amber:   "F4A261",
  white:   "F0F4F8",
  muted:   "8A9BB5",
  dim:     "5A6A85",
  green:   "2EC4B6",
  purple:  "7C6FCD",
  red:     "E87D73",
};

const makeShadow = () => ({
  type: "outer", color: "000000", blur: 8, offset: 2, angle: 135, opacity: 0.22
});

// ─── Helpers ─────────────────────────────────────────────────────────────────
function addCard(slide, x, y, w, h, accent) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow()
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w: 0.06, h,
    fill: { color: accent }, line: { color: accent }
  });
}

function addDivider(slide) {
  slide.addShape(pres.shapes.LINE, {
    x: 0.45, y: 0.88, w: 9.1, h: 0,
    line: { color: C.dim, width: 0.5 }
  });
}

function addSectionLabel(slide, text, x = 0.45, y = 0.15) {
  slide.addText(text, {
    x, y, w: 6, h: 0.22,
    fontSize: 8.5, fontFace: "Calibri", color: C.teal,
    bold: true, charSpacing: 3, margin: 0
  });
}

function addTitle(slide, text, x = 0.45, y = 0.26, w = 9.1) {
  slide.addText(text, {
    x, y, w, h: 0.58,
    fontSize: 30, fontFace: "Trebuchet MS",
    color: C.white, bold: true, margin: 0
  });
}

function bulletItems(arr) {
  return arr.map((t, i) => ({
    text: t,
    options: i < arr.length - 1
      ? { bullet: true, breakLine: true }
      : { bullet: true }
  }));
}

// ─── SLIDE 1 — Title ─────────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  // top/bottom bars
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.07, fill: { color: C.teal }, line: { color: C.teal } });
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.555, w: 10, h: 0.07, fill: { color: C.amber }, line: { color: C.amber } });

  // film sprocket holes (decorative)
  for (let i = 0; i < 5; i++) {
    s.addShape(pres.shapes.RECTANGLE, { x: 0.1, y: 0.35 + i * 1.05, w: 0.24, h: 0.7, fill: { color: C.card2 }, line: { color: C.dim, width: 0.5 } });
    s.addShape(pres.shapes.RECTANGLE, { x: 9.66, y: 0.35 + i * 1.05, w: 0.24, h: 0.7, fill: { color: C.card2 }, line: { color: C.dim, width: 0.5 } });
  }

  s.addText("Film Digitization System", {
    x: 0.7, y: 1.45, w: 8.6, h: 1.0,
    fontSize: 46, fontFace: "Trebuchet MS",
    color: C.white, bold: true, align: "center", margin: 0
  });
  s.addText("Software Pipeline:  Scan  ·  Stitch  ·  Post-Process", {
    x: 0.7, y: 2.6, w: 8.6, h: 0.5,
    fontSize: 19, fontFace: "Calibri", color: C.teal, align: "center", margin: 0
  });
  s.addShape(pres.shapes.LINE, { x: 3.5, y: 3.25, w: 3.0, h: 0, line: { color: C.dim, width: 1 } });
  s.addText("ECE 445   ·   Raspberry Pi HQ Camera (IMX477)   ·   STM32 MCU   ·   Gemini AI", {
    x: 0.7, y: 3.45, w: 8.6, h: 0.35,
    fontSize: 10.5, fontFace: "Calibri", color: C.muted, align: "center", margin: 0
  });
}

// ─── SLIDE 2 — System Architecture ───────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "OVERVIEW");
  addTitle(s, "Software Pipeline Architecture");
  addDivider(s);

  const stages = [
    { n: "01", title: "SCAN",     color: C.teal,   desc: "RPi HQ Camera captures overlapping tiles; STM32 steps motorized film gate on each exposure" },
    { n: "02", title: "STITCH",   color: C.amber,  desc: "Feather-blend 40 tiles into a seamless full-frame mosaic using manifest-driven weight maps" },
    { n: "03", title: "PROCESS",  color: C.green,  desc: "Density-domain negative inversion + perceptual tone mapping + Gemini-driven iterative refinement" },
    { n: "04", title: "OUTPUT",   color: C.purple, desc: "Social-media-ready JPEG + metadata JSON report; cloud upload via AWS S3 + Lambda" },
  ];

  const bW = 2.1, bH = 3.55, startX = 0.35, gap = 0.2, baseY = 1.05;
  stages.forEach((st, i) => {
    const x = startX + i * (bW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y: baseY, w: bW, h: bH, fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: baseY, w: bW, h: 0.07, fill: { color: st.color }, line: { color: st.color } });
    s.addText(st.n, { x, y: baseY + 0.15, w: bW, h: 0.44, fontSize: 26, fontFace: "Trebuchet MS", color: st.color, bold: true, align: "center", margin: 0 });
    s.addText(st.title, { x, y: baseY + 0.65, w: bW, h: 0.38, fontSize: 13, fontFace: "Trebuchet MS", color: C.white, bold: true, charSpacing: 2, align: "center", margin: 0 });
    s.addText(st.desc, { x: x + 0.12, y: baseY + 1.15, w: bW - 0.24, h: 2.2, fontSize: 11, fontFace: "Calibri", color: C.muted, margin: 0 });
    if (i < stages.length - 1) {
      s.addShape(pres.shapes.LINE, { x: x + bW + 0.03, y: baseY + bH / 2, w: gap - 0.05, h: 0, line: { color: C.dim, width: 1.5, endArrowType: "open" } });
    }
  });

  s.addText("Hardware: STM32 MCU controls dual LED arrays (backlight / frontlight) and motorized film gate", {
    x: 0.45, y: 4.92, w: 9.1, h: 0.28,
    fontSize: 9, fontFace: "Calibri", color: C.dim, align: "center", margin: 0
  });
}

// ─── SLIDE 3 — Scanning ───────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "STAGE 01  —  SCANNING");
  addTitle(s, "Tile Capture & Coordination");
  addDivider(s);

  const steps = [
    { n: "1", text: "Browser sends POST /scan → FastAPI server.py triggers job", sub: "server.py" },
    { n: "2", text: "ScanJobCoordinator starts background thread; state machine: Ready → Working", sub: "coordinator.py" },
    { n: "3", text: "STM32 steps film gate; RPi HQ Camera captures each tile (IMX477, 12 MP)", sub: "serial_comm.py  ·  MockSerialInterface fallback for dev" },
    { n: "4", text: "Each tile saved with row/col position metadata to manifest.csv", sub: "tiles/  +  manifest.csv" },
    { n: "5", text: "Frontend polls GET /status at 500 ms; receives state + output URLs on done", sub: "software/web/app.js" },
  ];

  steps.forEach((st, i) => {
    const y = 1.1 + i * 0.82;
    s.addShape(pres.shapes.OVAL, { x: 0.45, y, w: 0.38, h: 0.38, fill: { color: C.teal }, line: { color: C.teal } });
    s.addText(st.n, { x: 0.45, y, w: 0.38, h: 0.38, fontSize: 12, fontFace: "Trebuchet MS", color: C.bg, bold: true, align: "center", valign: "middle", margin: 0 });
    if (i < steps.length - 1) {
      s.addShape(pres.shapes.LINE, { x: 0.635, y: y + 0.38, w: 0, h: 0.44, line: { color: C.dim, width: 1 } });
    }
    s.addText(st.text, { x: 1.0, y: y - 0.02, w: 4.85, h: 0.33, fontSize: 11.5, fontFace: "Calibri", color: C.white, bold: true, margin: 0 });
    s.addText(st.sub, { x: 1.0, y: y + 0.3, w: 4.85, h: 0.22, fontSize: 8.5, fontFace: "Calibri", color: C.teal, margin: 0 });
  });

  // Tile grid visualization
  const gX = 6.35, gY = 1.05;
  s.addText("40-tile capture grid (5 × 8)", {
    x: gX, y: gY - 0.32, w: 3.4, h: 0.26,
    fontSize: 9.5, fontFace: "Calibri", color: C.muted, align: "center", margin: 0
  });

  const tW = 0.44, tH = 0.33, ovX = 0.07, ovY = 0.06;
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 5; c++) {
      const isHot = r === 2 && c === 2;
      s.addShape(pres.shapes.RECTANGLE, {
        x: gX + c * (tW - ovX), y: gY + r * (tH - ovY),
        w: tW, h: tH,
        fill: { color: isHot ? C.teal : C.card2 },
        line: { color: isHot ? C.tealDk : C.dim, width: 0.5 }
      });
    }
  }
  s.addText("← overlap region (feather zone)", {
    x: gX, y: gY + 8 * (tH - ovY) + 0.08, w: 3.4, h: 0.25,
    fontSize: 8, fontFace: "Calibri", color: C.amber, align: "center", margin: 0
  });
}

// ─── SLIDE 4 — Stitching ──────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "STAGE 02  —  STITCHING");
  addTitle(s, "Feather-Blended Tile Assembly");
  addDivider(s);

  // Left cards
  addCard(s, 0.45, 1.02, 4.5, 1.38, C.amber);
  s.addText("Weighted Pixel Accumulation", {
    x: 0.65, y: 1.10, w: 4.1, h: 0.3,
    fontSize: 12, fontFace: "Trebuchet MS", color: C.white, bold: true, margin: 0
  });
  s.addText(bulletItems([
    "Overlap zone: linear weight ramp  ε → 1.0 → ε",
    "Non-overlap zone: weight = 1.0",
    "output = Σ(pixel × weight) / Σ(weight)  →  seamless seams",
  ]), {
    x: 0.65, y: 1.44, w: 4.1, h: 0.88,
    fontSize: 11, fontFace: "Calibri", color: C.muted, margin: 0
  });

  addCard(s, 0.45, 2.55, 4.5, 1.18, C.teal);
  s.addText("manifest.csv Schema", {
    x: 0.65, y: 2.63, w: 4.1, h: 0.3,
    fontSize: 12, fontFace: "Trebuchet MS", color: C.white, bold: true, margin: 0
  });
  s.addText("row, col, filename, left, top, right, bottom, overlap_x_px, overlap_y_px\nTile positions define exact pixel placement on the full-frame canvas", {
    x: 0.65, y: 2.96, w: 4.1, h: 0.68,
    fontSize: 10.5, fontFace: "Consolas", color: C.muted, margin: 0
  });

  addCard(s, 0.45, 3.87, 4.5, 1.52, C.green);
  s.addText("Post-Stitch Enhancement Stages", {
    x: 0.65, y: 3.95, w: 4.1, h: 0.3,
    fontSize: 12, fontFace: "Trebuchet MS", color: C.white, bold: true, margin: 0
  });
  s.addText(bulletItems([
    "Invert  (255 − pixel)",
    "White Balance  (per-channel percentile gain)",
    "Auto-Levels  →  CLAHE (LAB L-channel)",
    "Cast-suppression mask  →  Selective Desaturation",
    "Unsharp Mask  (σ=1.2, amount=0.35)"
  ]), {
    x: 0.65, y: 4.3, w: 4.1, h: 1.0,
    fontSize: 10.5, fontFace: "Calibri", color: C.muted, margin: 0
  });

  // Right: weight map visual
  const wX = 5.35, wY = 1.02;
  s.addText("Weight profile across tile boundary:", {
    x: wX, y: wY, w: 4.3, h: 0.26,
    fontSize: 9.5, fontFace: "Calibri", color: C.muted, margin: 0
  });

  // 3 overlapping tiles
  const tW = 1.25, tH = 1.25, ov = 0.3;
  for (let i = 0; i < 3; i++) {
    const tx = wX + i * (tW - ov);
    s.addShape(pres.shapes.RECTANGLE, { x: tx, y: wY + 0.32, w: tW, h: tH, fill: { color: C.card2 }, line: { color: C.dim, width: 1 } });
    s.addText(`Tile ${i + 1}`, { x: tx + 0.08, y: wY + 0.38, w: 0.7, h: 0.25, fontSize: 9, fontFace: "Calibri", color: C.muted, margin: 0 });
    if (i > 0) {
      s.addShape(pres.shapes.RECTANGLE, { x: tx, y: wY + 0.32, w: ov, h: tH, fill: { color: C.amber, transparency: 58 }, line: { color: C.amber, width: 0.5 } });
      s.addText("ramp", { x: tx + 0.02, y: wY + 0.86, w: ov - 0.02, h: 0.22, fontSize: 7.5, fontFace: "Calibri", color: C.amber, align: "center", margin: 0 });
    }
  }

  // Weight value boxes
  const wBarY = wY + 1.72;
  const zones = [
    { label: "weight = 1.0", color: C.teal, x: wX, w: 0.9 },
    { label: "ramp", color: C.amber, x: wX + 0.9, w: 0.55 },
    { label: "weight = 1.0", color: C.teal, x: wX + 1.45, w: 0.9 },
    { label: "ramp", color: C.amber, x: wX + 2.35, w: 0.55 },
    { label: "weight = 1.0", color: C.teal, x: wX + 2.9, w: 0.9 },
  ];
  zones.forEach(z => {
    s.addShape(pres.shapes.RECTANGLE, { x: z.x, y: wBarY, w: z.w, h: 0.42, fill: { color: z.color, transparency: 35 }, line: { color: z.color, width: 0.5 } });
    s.addText(z.label, { x: z.x + 0.02, y: wBarY + 0.09, w: z.w - 0.04, h: 0.24, fontSize: 7.5, fontFace: "Calibri", color: C.white, align: "center", margin: 0 });
  });
  s.addText("← Tile A  ·  Overlap  ·  Tile B  ·  Overlap  ·  Tile C →", {
    x: wX, y: wBarY + 0.5, w: 3.8, h: 0.22,
    fontSize: 8, fontFace: "Calibri", color: C.dim, align: "center", margin: 0
  });

  // Output files
  addCard(s, wX, wY + 2.45, 4.3, 2.0, C.purple);
  s.addText("Output Files", { x: wX + 0.2, y: wY + 2.53, w: 3.9, h: 0.3, fontSize: 11, fontFace: "Trebuchet MS", color: C.white, bold: true, margin: 0 });
  s.addText(bulletItems([
    "stitched_raw.png",
    "inverted_positive.png",
    "white_balanced.png  ·  leveled.png",
    "desaturation_mask.png",
    "processed_negative.png  ← final"
  ]), {
    x: wX + 0.2, y: wY + 2.88, w: 3.9, h: 1.45,
    fontSize: 10, fontFace: "Calibri", color: C.muted, margin: 0
  });
}

// ─── SLIDE 5 — The Negative Film Challenge ────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.08, fill: { color: C.amber }, line: { color: C.amber } });
  addSectionLabel(s, "THE CORE CHALLENGE", 3.2, 0.22);

  s.addText("Why Negatives Are the Hardest Problem", {
    x: 0.5, y: 0.52, w: 9.0, h: 0.68,
    fontSize: 32, fontFace: "Trebuchet MS", color: C.white, bold: true, align: "center", margin: 0
  });

  const challenges = [
    {
      title: "Orange Mask",
      color: C.amber,
      points: [
        "Film base has a deep orange coupler dye",
        "Varies by brand, age, and chemistry",
        "Must be estimated from clear-base ROI",
        "Incorrect removal destroys color accuracy",
      ]
    },
    {
      title: "Inverted Tones & Dye Crosstalk",
      color: C.teal,
      points: [
        "Cyan/Magenta/Yellow dyes record complements",
        "Simple 255−x inversion creates severe casts",
        "Dye channels bleed into each other",
        "Must invert in optical-density space",
      ]
    },
    {
      title: "Non-linear Tone & Gamma",
      color: C.green,
      points: [
        "Film gamma ≠ digital display gamma",
        "Shadow/highlight zones behave non-linearly",
        "Perceptual tone mapping required (LAB)",
        "Auto-levels alone → flat, desaturated result",
      ]
    },
  ];

  challenges.forEach((ch, i) => {
    const x = 0.35 + i * 3.2;
    const y = 1.5;
    const w = 3.0, h = 3.9;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.07, fill: { color: ch.color }, line: { color: ch.color } });
    s.addText(ch.title, { x: x + 0.15, y: y + 0.18, w: w - 0.3, h: 0.42, fontSize: 13.5, fontFace: "Trebuchet MS", color: ch.color, bold: true, margin: 0 });
    s.addText(bulletItems(ch.points), {
      x: x + 0.15, y: y + 0.72, w: w - 0.3, h: 3.0,
      fontSize: 11, fontFace: "Calibri", color: C.muted, margin: 0
    });
  });
}

// ─── SLIDE 6 — Density-Domain Inversion ──────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "STAGE 03  —  NEGATIVE INVERSION");
  addTitle(s, "Density-Domain Inversion Pipeline");
  addDivider(s);

  const stages = [
    { n: "①", label: "Base\nCorrection",  desc: "frame ÷ base_rgb\nclips transmission\nto (ε, 1]", color: C.amber  },
    { n: "②", label: "Density\nCalc",      desc: "density =\n−log(transmission)\nper channel", color: C.teal   },
    { n: "③", label: "Dye\nUnmixing",     desc: "3 × 3 matrix\nseparates CMY\ndye channels", color: C.green  },
    { n: "④", label: "Expm1\nInvert",     desc: "(eˣ − 1) ×\ngains\n[1.35, 1.20, 1.10]", color: C.purple },
    { n: "⑤", label: "Color\nMapping",    desc: "gray anchor +\nempirical 3×3 +\nneutral damp", color: C.red    },
  ];

  const bW = 1.57, bH = 2.3, startX = 0.3, gap = 0.2, bY = 1.05;
  stages.forEach((st, i) => {
    const x = startX + i * (bW + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y: bY, w: bW, h: bH, fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: bY, w: bW, h: 0.06, fill: { color: st.color }, line: { color: st.color } });
    s.addText(st.n, { x, y: bY + 0.1, w: bW, h: 0.4, fontSize: 20, fontFace: "Trebuchet MS", color: st.color, bold: true, align: "center", margin: 0 });
    s.addText(st.label, { x, y: bY + 0.55, w: bW, h: 0.52, fontSize: 11, fontFace: "Trebuchet MS", color: C.white, bold: true, align: "center", margin: 0 });
    s.addText(st.desc, { x: x + 0.1, y: bY + 1.12, w: bW - 0.2, h: 1.1, fontSize: 9.5, fontFace: "Calibri", color: C.muted, align: "center", margin: 0 });
    if (i < stages.length - 1) {
      s.addShape(pres.shapes.LINE, { x: x + bW + 0.03, y: bY + bH / 2, w: gap - 0.05, h: 0, line: { color: C.dim, width: 1.5, endArrowType: "open" } });
    }
  });

  // Bottom two insight cards
  addCard(s, 0.45, 3.52, 4.35, 1.9, C.teal);
  s.addText("Key Insight: Optical Density Space", {
    x: 0.65, y: 3.60, w: 3.95, h: 0.32,
    fontSize: 12, fontFace: "Trebuchet MS", color: C.teal, bold: true, margin: 0
  });
  s.addText(bulletItems([
    "OD = −log(light transmission through film)",
    "Dye concentrations add linearly in OD (Beer-Lambert)",
    "Preserves color relationships across exposures",
    "Simple RGB inversion breaks this — OD space fixes it",
  ]), {
    x: 0.65, y: 3.96, w: 3.95, h: 1.38,
    fontSize: 10.5, fontFace: "Calibri", color: C.muted, margin: 0
  });

  addCard(s, 5.05, 3.52, 4.5, 1.9, C.amber);
  s.addText("Conservative Dye Unmixing Matrix", {
    x: 5.25, y: 3.60, w: 4.1, h: 0.32,
    fontSize: 12, fontFace: "Trebuchet MS", color: C.amber, bold: true, margin: 0
  });
  s.addText([
    { text: "  [ 1.55  −0.38  −0.17 ]", options: { breakLine: true } },
    { text: "  [ −0.22   1.44  −0.22 ]", options: { breakLine: true } },
    { text: "  [ −0.08  −0.45   1.53 ]", options: { breakLine: true } },
    { text: "Blended with identity matrix by strength", options: {} },
  ], {
    x: 5.25, y: 3.96, w: 4.1, h: 1.38,
    fontSize: 10.5, fontFace: "Consolas", color: C.muted, margin: 0
  });
}

// ─── SLIDE 7 — Physical RAW Pipeline ─────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "STAGE 03  —  PHYSICAL RAW PIPELINE");
  addTitle(s, "RAW-First 10-Stage Correction");
  addDivider(s);

  const left = [
    { tag: "DNG", title: "RAW Bayer Ingest",            desc: "rawpy loads .dng; extracts Bayer mosaic + black/white level metadata → float32",            color: C.dim    },
    { tag: "S1",  title: "Flat-Field Correction",       desc: "Multi-scale Gaussian models backlight illumination map per CFA plane; corrected = frame ÷ illum^strength",  color: C.teal   },
    { tag: "S2",  title: "Demosaic → RGB",              desc: "Bilinear Bayer interpolation → linear float32 RGB; diagnostic preview saved",              color: C.teal   },
    { tag: "S3",  title: "Base Reference Extraction",   desc: "Median RGB of clear-base ROI (p10–p90 luma) from reference_layout.json",                  color: C.amber  },
    { tag: "S4",  title: "Base Correction + Density",   desc: "frame ÷ base_rgb → transmission; −log(T) → optical density; percentile normalize",        color: C.amber  },
  ];

  const right = [
    { tag: "S5",  title: "Dye Unmixing",                desc: "3×3 matrix in density domain blended with identity; separates CMY channels",               color: C.green  },
    { tag: "S6",  title: "Expm1 Inversion",             desc: "(eˣ − 1) × per-channel gains [1.35, 1.20, 1.10] → positive image",                        color: C.green  },
    { tag: "S7",  title: "Soft Reference Mapping",      desc: "Gray anchor (0.35) → empirical 3×3 → neutral damp → red guard; 3 presets",                 color: C.purple },
    { tag: "S8+", title: "Perceptual Tone + Color Ref", desc: "LAB zoned tone curve; pseudo-LUT hue/chroma adjust; neutral-region protection",             color: C.purple },
    { tag: "OUT", title: "Final Finish & Export",        desc: "sRGB OETF; exposure/contrast/temp/tint; grain + bloom + sharpening → PNG + JSON report",   color: C.red    },
  ];

  const rH = 0.82, gap = 0.06, cW = 4.6;
  const renderCol = (arr, cx) => {
    arr.forEach((r, i) => {
      const y = 1.0 + i * (rH + gap);
      addCard(s, cx, y, cW, rH, r.color);
      s.addShape(pres.shapes.RECTANGLE, { x: cx + 0.06, y: y + 0.06, w: 0.56, h: 0.28, fill: { color: r.color }, line: { color: r.color } });
      s.addText(r.tag, { x: cx + 0.06, y: y + 0.06, w: 0.56, h: 0.28, fontSize: 8, fontFace: "Trebuchet MS", color: C.bg, bold: true, align: "center", valign: "middle", margin: 0 });
      s.addText(r.title, { x: cx + 0.72, y: y + 0.07, w: cW - 0.82, h: 0.28, fontSize: 11, fontFace: "Trebuchet MS", color: C.white, bold: true, margin: 0 });
      s.addText(r.desc, { x: cx + 0.72, y: y + 0.38, w: cW - 0.82, h: 0.38, fontSize: 9, fontFace: "Calibri", color: C.muted, margin: 0 });
    });
  };

  renderCol(left, 0.4);
  renderCol(right, 5.1);
}

// ─── SLIDE 8 — Agentic AI Loop ────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "STAGE 03  —  AGENTIC AI REFINEMENT");
  addTitle(s, "Gemini-Powered Iterative Refinement");
  addDivider(s);

  const agents = [
    {
      title: "ClassifierAgent",
      color: C.teal,
      role: "Film Type Detection",
      pts: [
        "Gemini VLM → JSON: film_type",
        "Types: negative_35mm, negative_120, positive_35mm, positive_120",
        "Fallback: aspect ratio + luma/saturation heuristics",
      ]
    },
    {
      title: "PostProcessingAgent",
      color: C.amber,
      role: "13-Stage Negative Pipeline",
      pts: [
        "Routes by film type to correct pipeline",
        "Negative: density inversion → gray-world WB → LAB neutralization → CLAHE → vibrance → unsharp",
        "Positive: lighter 7-stage pipeline",
      ]
    },
    {
      title: "EvaluatorAgent",
      color: C.green,
      role: "Quality Scoring & Param Tuning",
      pts: [
        "9 metrics: gray_mean, clipping ratios, channel_spread, saturation, laplacian_variance",
        "Gemini scores image 0–1 + autonomously tunes 14 parameters",
        "Loop until score ≥ 0.75 or max iterations reached",
      ]
    },
  ];

  agents.forEach((ag, i) => {
    const x = 0.35 + i * 3.15, y = 1.05, w = 2.95, h = 3.42;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.07, fill: { color: ag.color }, line: { color: ag.color } });
    s.addText(ag.title, { x: x + 0.12, y: y + 0.16, w: w - 0.24, h: 0.38, fontSize: 13, fontFace: "Trebuchet MS", color: ag.color, bold: true, margin: 0 });
    s.addText(ag.role, { x: x + 0.12, y: y + 0.58, w: w - 0.24, h: 0.3, fontSize: 10, fontFace: "Calibri", color: C.white, bold: true, margin: 0 });
    s.addText(bulletItems(ag.pts), { x: x + 0.12, y: y + 0.98, w: w - 0.24, h: 2.35, fontSize: 10.5, fontFace: "Calibri", color: C.muted, margin: 0 });
    if (i < agents.length - 1) {
      s.addShape(pres.shapes.LINE, { x: x + w + 0.04, y: y + h / 2, w: 0.11, h: 0, line: { color: C.dim, width: 1.5, endArrowType: "open" } });
    }
  });

  // Feedback loop banner
  addCard(s, 0.45, 4.62, 9.1, 0.78, C.purple);
  s.addText("Feedback Loop", { x: 0.65, y: 4.70, w: 2.0, h: 0.28, fontSize: 11, fontFace: "Trebuchet MS", color: C.purple, bold: true, margin: 0 });
  s.addText("if score < 0.75  →  EvaluatorAgent suggests new params  →  PostProcessingAgent re-runs  →  EvaluatorAgent re-scores  →  repeat (max_iter)", {
    x: 0.65, y: 5.0, w: 8.6, h: 0.28,
    fontSize: 10, fontFace: "Calibri", color: C.muted, margin: 0
  });
  s.addText("Output:  <stem>_processed.png  +  <stem>_meta.json  (score, metrics, params, iteration log)", {
    x: 4.4, y: 4.70, w: 4.9, h: 0.28,
    fontSize: 9.5, fontFace: "Calibri", color: C.green, margin: 0
  });
}

// ─── SLIDE 9 — Quality Metrics ────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  addSectionLabel(s, "QUALITY ASSURANCE");
  addTitle(s, "Evaluator Quality Metrics  &  14 Tunable Parameters");
  addDivider(s);

  const metrics = [
    { name: "gray_mean",       ideal: "100 – 165",  desc: "Overall exposure level",      color: C.teal   },
    { name: "gray_std",        ideal: "> 30",        desc: "Tonal contrast present",      color: C.teal   },
    { name: "clipped_black",   ideal: "< 0.5 %",    desc: "No crushed shadows",          color: C.amber  },
    { name: "clipped_white",   ideal: "< 0.5 %",    desc: "No blown highlights",         color: C.amber  },
    { name: "blue_red_delta",  ideal: "≈ 0",         desc: "Color balance neutral",       color: C.green  },
    { name: "channel_spread",  ideal: "< 0.10",      desc: "No dominant color cast",      color: C.green  },
    { name: "mean_saturation", ideal: "0.08 – 0.45", desc: "Natural color richness",      color: C.purple },
    { name: "laplacian_var",   ideal: "> 150",       desc: "Image sharpness proxy",       color: C.purple },
    { name: "quality_score",   ideal: "≥ 0.75",      desc: "Gemini VLM composite grade",  color: C.red    },
  ];

  const cols = 3, mW = 2.87, mH = 1.06, gX = 0.17, gY = 0.13;
  metrics.forEach((m, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = 0.45 + col * (mW + gX), y = 1.05 + row * (mH + gY);
    addCard(s, x, y, mW, mH, m.color);
    s.addText(m.name, { x: x + 0.16, y: y + 0.08, w: mW - 0.26, h: 0.3, fontSize: 11, fontFace: "Consolas", color: m.color, bold: true, margin: 0 });
    s.addText(`Ideal: ${m.ideal}`, { x: x + 0.16, y: y + 0.42, w: mW - 0.26, h: 0.26, fontSize: 10.5, fontFace: "Calibri", color: C.white, margin: 0 });
    s.addText(m.desc, { x: x + 0.16, y: y + 0.69, w: mW - 0.26, h: 0.27, fontSize: 9, fontFace: "Calibri", color: C.muted, margin: 0 });
  });

  // 14 params note
  addCard(s, 0.45, 4.62, 9.1, 0.75, C.teal);
  s.addText("14 Autonomously Tuned Parameters:", { x: 0.65, y: 4.70, w: 3.5, h: 0.28, fontSize: 11, fontFace: "Trebuchet MS", color: C.teal, bold: true, margin: 0 });
  s.addText("wb_clip_percent  ·  black_point  ·  white_point  ·  clahe_clip  ·  clahe_grid  ·  unsharp_amount  ·  unsharp_sigma  ·  desat_strength  ·  shadow_lift  ·  gamma  ·  lab_strength  ·  highlight_compression  ·  vibrance  ·  desat_sigma", {
    x: 0.65, y: 5.0, w: 8.6, h: 0.28,
    fontSize: 9.5, fontFace: "Consolas", color: C.muted, margin: 0
  });
}

// ─── SLIDE 10 — Summary ───────────────────────────────────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 0.07, fill: { color: C.teal }, line: { color: C.teal } });
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 5.555, w: 10, h: 0.07, fill: { color: C.amber }, line: { color: C.amber } });

  s.addText("TECHNICAL SUMMARY", { x: 0.5, y: 0.22, w: 9.0, h: 0.28, fontSize: 9, fontFace: "Calibri", color: C.teal, align: "center", bold: true, charSpacing: 3, margin: 0 });
  s.addText("From Film to Feed", { x: 0.5, y: 0.6, w: 9.0, h: 0.75, fontSize: 42, fontFace: "Trebuchet MS", color: C.white, bold: true, align: "center", margin: 0 });

  const achievements = [
    { num: "40",   unit: "tiles",   detail: "feather-blended into\nseamless mosaic",       color: C.teal   },
    { num: "10+",  unit: "stages",  detail: "physically-grounded\nRAW pipeline",            color: C.amber  },
    { num: "14",   unit: "params",  detail: "tuned autonomously\nby Gemini VLM",            color: C.green  },
    { num: "9",    unit: "metrics", detail: "exposure, color,\nsharpness, clipping",        color: C.purple },
  ];

  achievements.forEach((a, i) => {
    const x = 0.35 + i * 2.35, y = 1.65;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 2.15, h: 2.35, fill: { color: C.card }, line: { color: C.card }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: 2.15, h: 0.07, fill: { color: a.color }, line: { color: a.color } });
    s.addText(a.num, { x, y: y + 0.12, w: 2.15, h: 0.75, fontSize: 52, fontFace: "Trebuchet MS", color: a.color, bold: true, align: "center", margin: 0 });
    s.addText(a.unit, { x, y: y + 0.88, w: 2.15, h: 0.3, fontSize: 14, fontFace: "Calibri", color: C.white, align: "center", margin: 0 });
    s.addText(a.detail, { x: x + 0.1, y: y + 1.25, w: 1.95, h: 0.9, fontSize: 10, fontFace: "Calibri", color: C.muted, align: "center", margin: 0 });
  });

  // Pipeline recap
  s.addShape(pres.shapes.LINE, { x: 0.45, y: 4.2, w: 9.1, h: 0, line: { color: C.dim, width: 0.5 } });
  s.addText([
    { text: "Scan  ", options: { color: C.teal, bold: true } },
    { text: "→  tile capture via RPi + STM32        ", options: { color: C.muted } },
    { text: "Stitch  ", options: { color: C.amber, bold: true } },
    { text: "→  feather-blend 40 tiles        ", options: { color: C.muted } },
    { text: "Process  ", options: { color: C.green, bold: true } },
    { text: "→  density inversion + AI refinement", options: { color: C.muted } },
  ], {
    x: 0.45, y: 4.32, w: 9.1, h: 0.45,
    fontSize: 11.5, fontFace: "Calibri", align: "center", margin: 0
  });

  s.addText("ECE 445  ·  Film Digitization System  ·  Post-Processing Team", {
    x: 0.5, y: 5.2, w: 9.0, h: 0.25,
    fontSize: 9, fontFace: "Calibri", color: C.dim, align: "center", margin: 0
  });
}

pres.writeFile({ fileName: "film_digitizer_demo.pptx" });
console.log("Done → film_digitizer_demo.pptx");
