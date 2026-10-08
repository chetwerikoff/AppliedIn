// appliedin.dev: three live lanes of job postings, one per search option.
//
// Each lane sits above its column (Fresh, Career Ops, Cosign network) and drifts
// upward like a feed. Every couple of seconds one posting is picked: it lifts
// forward and takes a green edge with its match score, then settles back. Cards
// are drawn on canvas at 2x in the site's own type and colours, so they read as
// the product rather than as decoration. Pauses off screen; a still frame for
// reduced motion.
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.min.js";

const host = document.getElementById("boards3d");
const canvas = host && host.querySelector("canvas");
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

function supported() {
  try { return !!(canvas && (canvas.getContext("webgl2") || canvas.getContext("webgl"))); } catch { return false; }
}

const LANES = [
  { // Fresh: companies on your watchlist
    speed: 0.16,
    posts: [
      ["Stripe", "Staff Backend Engineer, Payments", "Seattle", "Greenhouse"],
      ["Linear", "Product Engineer", "Remote, US", "Ashby"],
      ["Vercel", "Backend Engineer, Infrastructure", "Remote", "Greenhouse"],
      ["Figma", "Senior Engineer, Multiplayer", "San Francisco", "Greenhouse"],
      ["Databricks", "Distributed Systems Engineer", "Seattle", "Greenhouse"],
      ["Notion", "Software Engineer, Data", "New York", "Ashby"],
    ],
  },
  { // Career Ops: companies you don't track yet
    speed: 0.12,
    posts: [
      ["Honeycomb", "Senior Software Engineer II", "Remote, US", "Greenhouse"],
      ["Toast", "Staff Engineer, Care Automation", "Remote, US", "Greenhouse"],
      ["Maven Clinic", "Staff Software Engineer, AI/ML", "New York", "Lever"],
      ["CaptivateIQ", "Staff Engineer, AI Platform", "Remote", "Lever"],
      ["Docker", "Principal Engineer, AI Tools", "Seattle", "Ashby"],
      ["Workday", "Senior Engineer, Agent Platform", "Pleasanton", "Workday"],
    ],
  },
  { // Cosign network: a large index across many boards
    speed: 0.2,
    posts: [
      ["Snorkel AI", "Senior Staff Engineer, AI/ML", "San Francisco", "Greenhouse"],
      ["Replit", "Staff Engineer, Agentic Ads", "Foster City", "Ashby"],
      ["Instacart", "Senior Engineer II, Ads Quality", "Remote, US", "Greenhouse"],
      ["NVIDIA", "Senior Engineer, Security", "Santa Clara", "Workday"],
      ["Thumbtack", "Senior Engineer, Design Systems", "Remote", "Ashby"],
      ["Athelas", "Senior Engineer, Air AI", "Mountain View", "Ashby"],
    ],
  },
];

if (host && supported()) {
  const root = document.documentElement;
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 2, 0.1, 100);
  const world = new THREE.Group();
  scene.add(world);

  // World width is fixed; the camera backs off to fit it, so each lane sits
  // over its column whatever the canvas size.
  const WIDTH = 12, CARD_W = 3.5;
  // Artwork is drawn at about twice its on-screen size: a 320 by 76 card on a
  // desktop becomes a 640 by 152 texture, crisp without wasting memory.
  const TW = 640, TH = 152, CARD_H = CARD_W * (TH / TW), GAP = 0.16;
  const STEP = CARD_H + GAP;

  // ---- card artwork -------------------------------------------------------
  const FONT = '"Schibsted Grotesk", system-ui, sans-serif';
  function drawCard(ctx, [company, role, place, board], picked) {
    const r = 20, pad = 22;
    ctx.clearRect(0, 0, TW, TH);
    const card = css("--card") || "#fff", rule = css("--rule") || "#ddd", ink = css("--ink") || "#111";
    const graphite = css("--graphite") || "#555", faint = css("--faint") || "#888", green = css("--green") || "#0b8f59";
    const paper2 = css("--paper_2") || "#eee";
    ctx.beginPath(); ctx.roundRect(2, 2, TW - 4, TH - 4, r);
    ctx.fillStyle = card; ctx.fill();
    ctx.lineWidth = picked ? 4 : 2; ctx.strokeStyle = picked ? green : rule; ctx.stroke();
    // Company mark
    const m = 60, my = (TH - m) / 2;
    ctx.beginPath(); ctx.roundRect(pad, my, m, m, 14); ctx.fillStyle = paper2; ctx.fill();
    ctx.fillStyle = graphite; ctx.font = `700 28px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(company.slice(0, 1), pad + m / 2, my + m / 2 + 1);
    // Role on the first line; company, place and board on the second.
    const x = pad + m + 18, right = TW - pad;
    const fit = (text, font, w) => {
      ctx.font = font; if (ctx.measureText(text).width <= w) return text;
      let t = text; while (t.length > 3 && ctx.measureText(t + "…").width > w) t = t.slice(0, -1); return t + "…";
    };
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    let pillW = 0;
    if (picked) {
      const label = "9/10 match";
      ctx.font = `700 22px ${FONT}`;
      pillW = ctx.measureText(label).width + 26;
      const bx = right - pillW, by = 26;
      ctx.beginPath(); ctx.roundRect(bx, by, pillW, 36, 18); ctx.fillStyle = green; ctx.fill();
      ctx.fillStyle = card; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(label, bx + pillW / 2, by + 19);
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    }
    ctx.fillStyle = ink;
    ctx.fillText(fit(role, `600 31px ${FONT}`, right - x - (pillW ? pillW + 12 : 0)), x, 62);
    const via = `via ${board}`;
    ctx.font = `400 24px ${FONT}`; const viaW = ctx.measureText(via).width;
    ctx.fillStyle = faint; ctx.fillText(via, right - viaW, 108);
    ctx.fillStyle = graphite; ctx.fillText(fit(`${company}, ${place}`, `400 26px ${FONT}`, right - x - viaW - 16), x, 108);
  }

  // ---- cards --------------------------------------------------------------
  const cards = [], PER_LANE = 8;
  const geometry = new THREE.PlaneGeometry(CARD_W, CARD_H);
  LANES.forEach((lane, li) => {
    const x = (li - 1) * (WIDTH / 3);
    // Eight cards a lane, cycling its postings, so the feed never shows a gap
    // at narrower widths where more of the lane is in view.
    Array.from({ length: PER_LANE }, (_, pi) => lane.posts[pi % lane.posts.length]).forEach((post, pi) => {
      const art = document.createElement("canvas"); art.width = TW; art.height = TH;
      const texture = new THREE.CanvasTexture(art);
      texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4;
      const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false });
      const mesh = new THREE.Mesh(geometry, material);
      world.add(mesh);
      cards.push({ mesh, texture, art, post, lane: li, x, offset: pi * STEP, picked: 0, lift: 0 });
    });
  });
  const LOOP = PER_LANE * STEP;

  function redraw(card) {
    drawCard(card.art.getContext("2d"), card.post, card.picked > 0);
    card.texture.needsUpdate = true;
  }
  const redrawAll = () => cards.forEach(redraw);

  // ---- layout -------------------------------------------------------------
  let halfH = 2;
  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const dist = (WIDTH / camera.aspect) / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
    camera.position.set(0, 0, dist * 1.08);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    halfH = (WIDTH / camera.aspect) / 2;
  }

  // ---- motion ---------------------------------------------------------------
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  host.addEventListener("pointermove", (e) => {
    const r = host.getBoundingClientRect();
    pointer.tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
    pointer.ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
  });
  host.addEventListener("pointerleave", () => { pointer.tx = pointer.ty = 0; });

  const smooth = (a, b, v) => { const t = Math.min(1, Math.max(0, (v - a) / (b - a))); return t * t * (3 - 2 * t); };
  let last = 0, nextPick = 1200, seed = 3;
  const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;

  function place(now, dt) {
    for (const c of cards) {
      c.offset = (c.offset + LANES[c.lane].speed * dt) % LOOP;
      const y = -halfH - STEP + c.offset;
      if (c.picked && now > c.picked) { c.picked = 0; redraw(c); }
      const lift = c.picked ? 1 : 0;
      c.lift += (lift - c.lift) * Math.min(1, dt * 5);
      c.mesh.position.set(c.x, y, c.lift * 0.55);
      c.mesh.scale.setScalar(1 + c.lift * 0.04);
      // Fade in from the bottom edge and out at the top, like a feed.
      // Fully faded before either edge, so no card is ever cut by the canvas.
      c.mesh.material.opacity = smooth(-halfH + CARD_H * 0.5, -halfH + CARD_H * 0.5 + STEP * 1.1, y)
        * (1 - smooth(halfH - CARD_H * 0.5 - STEP * 1.1, halfH - CARD_H * 0.5, y));
    }
  }

  function pick(now) {
    // Only a card fully in view, and one lane at a time.
    const inView = cards.filter((c) => !c.picked && Math.abs(c.mesh.position.y) < halfH - CARD_H);
    if (!inView.length) return;
    const c = inView[Math.floor(rand() * inView.length)];
    c.picked = now + 2200; redraw(c);
  }

  let running = false, raf = 0;
  function frame(now) {
    if (!running) return;
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 0; last = now;
    if (now > nextPick) { pick(now); nextPick = now + 2400 + rand() * 900; }
    place(now, dt);
    pointer.x += (pointer.tx - pointer.x) * 0.05; pointer.y += (pointer.ty - pointer.y) * 0.05;
    world.rotation.x = -0.05 + pointer.y * 0.02;
    world.rotation.y = pointer.x * 0.025;
    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  }
  const start = () => { if (!running && !still) { running = true; last = 0; raf = requestAnimationFrame(frame); } };
  const stop = () => { running = false; cancelAnimationFrame(raf); };

  function drawStill() {
    place(0, 0);
    world.rotation.x = -0.05;
    const c = cards.find((k) => k.lane === 2 && Math.abs(k.mesh.position.y) < halfH - CARD_H);
    if (c) { c.picked = Infinity; c.lift = 1; redraw(c); place(0, 0); }
    renderer.render(scene, camera);
  }

  const boot = () => {
    resize(); redrawAll();
    new ResizeObserver(() => { resize(); if (!running) (still ? drawStill() : renderer.render(scene, camera)); }).observe(host);
    new MutationObserver(() => { redrawAll(); if (!running) renderer.render(scene, camera); })
      .observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    if (still) drawStill();
    else {
      new IntersectionObserver(([e]) => (e.isIntersecting ? start() : stop()), { rootMargin: "80px" }).observe(host);
      document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    }
    host.classList.add("ready");
  };
  // Cards are drawn with the page's font, so wait for it rather than drawing twice.
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(boot);
}
