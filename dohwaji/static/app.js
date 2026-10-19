"use strict";

const canvas = document.querySelector("#canvas");
const viewport = document.querySelector("#viewport");
const folderName = document.querySelector("#folder-name");
const imageCount = document.querySelector("#image-count");
const saveStatus = document.querySelector("#save-status");
const imageTemplate = document.querySelector("#image-template");
const selectionBox = document.querySelector("#selection-box");

const GRID_COLUMNS = 5;
const GRID_X = 300;
const GRID_Y = 270;
const GRID_OFFSET_X = 64;
const GRID_OFFSET_Y = 64;
const DRAG_THRESHOLD = 3;

const items = new Map();
const selectedNames = new Set();
let highestZ = 0;
let drag = null;
let marquee = null;
let saveTimer = null;

function initialPosition(index) {
  return {
    x: GRID_OFFSET_X + (index % GRID_COLUMNS) * GRID_X,
    y: GRID_OFFSET_Y + Math.floor(index / GRID_COLUMNS) * GRID_Y,
    z: index + 1,
  };
}

function finiteNumber(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
}

function setTilePosition(item) {
  item.element.style.transform = `translate(${item.x}px, ${item.y}px)`;
  item.element.style.zIndex = String(item.z);
}

function expandCanvas() {
  let width = Math.max(2400, viewport.clientWidth * 2);
  let height = Math.max(1800, viewport.clientHeight * 2);

  for (const item of items.values()) {
    width = Math.max(width, item.x + 420);
    height = Math.max(height, item.y + 360);
  }

  canvas.style.width = `${Math.ceil(width)}px`;
  canvas.style.height = `${Math.ceil(height)}px`;
}

function applySelection(names) {
  selectedNames.clear();
  for (const name of names) selectedNames.add(name);

  for (const [name, item] of items) {
    const isSelected = selectedNames.has(name);
    item.element.classList.toggle("selected", isSelected);
    item.element.setAttribute("aria-pressed", String(isSelected));
  }
}

function bringSelectionToFront() {
  const selectedItems = [...selectedNames]
    .map((name) => items.get(name))
    .filter(Boolean)
    .sort((left, right) => left.z - right.z);

  for (const item of selectedItems) {
    highestZ += 1;
    item.z = highestZ;
    setTilePosition(item);
  }
}

function pointerDown(event, item) {
  if (event.button !== 0) return;

  event.preventDefault();
  item.element.focus({ preventScroll: true });
  if (!selectedNames.has(item.image.name)) {
    const nextSelection = event.shiftKey
      ? new Set([...selectedNames, item.image.name])
      : new Set([item.image.name]);
    applySelection(nextSelection);
  }

  bringSelectionToFront();
  const movingItems = [...selectedNames].map((name) => {
    const selectedItem = items.get(name);
    return { item: selectedItem, startX: selectedItem.x, startY: selectedItem.y };
  });

  drag = {
    primary: item,
    movingItems,
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    minimumX: Math.min(...movingItems.map((entry) => entry.startX)),
    minimumY: Math.min(...movingItems.map((entry) => entry.startY)),
    moved: false,
  };
  for (const entry of movingItems) entry.item.element.classList.add("dragging");
  item.element.setPointerCapture(event.pointerId);
}

function pointerMove(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;

  let dx = event.clientX - drag.startClientX;
  let dy = event.clientY - drag.startClientY;
  if (Math.hypot(dx, dy) >= DRAG_THRESHOLD) drag.moved = true;
  dx = Math.max(dx, -drag.minimumX);
  dy = Math.max(dy, -drag.minimumY);

  for (const entry of drag.movingItems) {
    entry.item.x = Math.round(entry.startX + dx);
    entry.item.y = Math.round(entry.startY + dy);
    setTilePosition(entry.item);
  }
  expandCanvas();
}

function pointerUp(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;

  const completed = drag;
  for (const entry of completed.movingItems) {
    entry.item.element.classList.remove("dragging");
    if (completed.moved) entry.item.suppressOpenUntil = Date.now() + 500;
  }
  if (completed.primary.element.hasPointerCapture(event.pointerId)) {
    completed.primary.element.releasePointerCapture(event.pointerId);
  }
  drag = null;
  scheduleSave(0);
}

function canvasPoint(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: event.clientX - bounds.left,
    y: event.clientY - bounds.top,
  };
}

function marqueeDown(event) {
  if (event.button !== 0 || event.target !== canvas) return;

  event.preventDefault();
  const origin = canvasPoint(event);
  const baseSelection = event.shiftKey ? new Set(selectedNames) : new Set();
  applySelection(baseSelection);
  marquee = {
    pointerId: event.pointerId,
    origin,
    baseSelection,
    moved: false,
  };
  selectionBox.hidden = true;
  canvas.setPointerCapture(event.pointerId);
}

function marqueeMove(event) {
  if (!marquee || event.pointerId !== marquee.pointerId) return;

  const point = canvasPoint(event);
  const left = Math.min(marquee.origin.x, point.x);
  const top = Math.min(marquee.origin.y, point.y);
  const right = Math.max(marquee.origin.x, point.x);
  const bottom = Math.max(marquee.origin.y, point.y);
  if (Math.hypot(point.x - marquee.origin.x, point.y - marquee.origin.y) >= DRAG_THRESHOLD) {
    marquee.moved = true;
  }

  selectionBox.hidden = !marquee.moved;
  selectionBox.style.transform = `translate(${left}px, ${top}px)`;
  selectionBox.style.width = `${right - left}px`;
  selectionBox.style.height = `${bottom - top}px`;

  const nextSelection = new Set(marquee.baseSelection);
  if (marquee.moved) {
    for (const [name, item] of items) {
      const itemRight = item.x + item.element.offsetWidth;
      const itemBottom = item.y + item.element.offsetHeight;
      const intersects = item.x < right && itemRight > left && item.y < bottom && itemBottom > top;
      if (intersects) nextSelection.add(name);
    }
  }
  applySelection(nextSelection);
}

function marqueeUp(event) {
  if (!marquee || event.pointerId !== marquee.pointerId) return;

  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  marquee = null;
  selectionBox.hidden = true;
}

function layoutPayload() {
  const positionedItems = {};
  for (const [name, item] of items) {
    positionedItems[name] = { x: item.x, y: item.y, z: item.z };
  }
  return { version: 1, items: positionedItems };
}

function setSaveStatus(message, state = "") {
  saveStatus.textContent = message;
  saveStatus.dataset.state = state;
}

async function saveLayout() {
  window.clearTimeout(saveTimer);
  saveTimer = null;
  setSaveStatus("Saving…");

  try {
    const response = await fetch("/api/layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(layoutPayload()),
    });
    if (!response.ok) throw new Error(`Save failed (${response.status})`);
    setSaveStatus("Saved");
  } catch (error) {
    console.error(error);
    setSaveStatus("Could not save layout", "error");
  }
}

function scheduleSave(delay = 180) {
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(saveLayout, delay);
}

function imageUrl(image) {
  return `/media?name=${encodeURIComponent(image.name)}`;
}

function openOriginal(image) {
  const originalTab = window.open(imageUrl(image), "_blank");
  if (originalTab) originalTab.opener = null;
}

function createTile(image, index, savedPosition) {
  const fallback = initialPosition(index);
  const position = savedPosition || fallback;
  const fragment = imageTemplate.content.cloneNode(true);
  const element = fragment.querySelector(".image-tile");
  const img = element.querySelector("img");
  const item = {
    image,
    element,
    x: Math.max(0, finiteNumber(position.x, fallback.x)),
    y: Math.max(0, finiteNumber(position.y, fallback.y)),
    z: Math.max(1, Math.round(finiteNumber(position.z, fallback.z))),
    suppressOpenUntil: 0,
  };

  img.src = imageUrl(image);
  img.alt = image.name;
  img.addEventListener("error", () => {
    element.title = `Could not load ${image.name}`;
  });
  element.title = `${image.name}\nDrag to arrange · Double-click to open original`;
  element.setAttribute("aria-label", `${image.name}. Drag to arrange; press Enter to open the original.`);
  element.setAttribute("aria-pressed", "false");
  element.addEventListener("pointerdown", (event) => pointerDown(event, item));
  element.addEventListener("pointermove", pointerMove);
  element.addEventListener("pointerup", pointerUp);
  element.addEventListener("pointercancel", pointerUp);
  element.addEventListener("dblclick", (event) => {
    event.preventDefault();
    if (Date.now() >= item.suppressOpenUntil) openOriginal(image);
  });
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      openOriginal(image);
    }
  });

  highestZ = Math.max(highestZ, item.z);
  items.set(image.name, item);
  setTilePosition(item);
  canvas.appendChild(fragment);
  return !savedPosition;
}

function showCanvasMessage(message, isError = false) {
  const paragraph = document.createElement("p");
  paragraph.className = isError ? "error-message" : "empty-message";
  paragraph.textContent = message;
  canvas.appendChild(paragraph);
}

async function start() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error(`Could not open Dohwaji (${response.status})`);
    const data = await response.json();

    folderName.textContent = data.folder;
    folderName.title = data.folderPath;
    document.title = `${data.folder} — Dohwaji`;
    imageCount.textContent = `${data.images.length} image${data.images.length === 1 ? "" : "s"}`;

    if (data.images.length === 0) {
      showCanvasMessage("This folder has no supported images. Dohwaji currently displays JPEG, PNG, WebP, GIF, and BMP files directly inside the selected folder.");
      setSaveStatus("");
      return;
    }

    let hasNewImages = false;
    data.images.forEach((image, index) => {
      const savedPosition = data.layout.items?.[image.name];
      hasNewImages = createTile(image, index, savedPosition) || hasNewImages;
    });
    expandCanvas();
    setSaveStatus("Ready");
    if (hasNewImages) scheduleSave();
  } catch (error) {
    console.error(error);
    folderName.textContent = "Could not open folder";
    showCanvasMessage(error.message, true);
    setSaveStatus("Error", "error");
  }
}

canvas.addEventListener("pointerdown", marqueeDown);
canvas.addEventListener("pointermove", marqueeMove);
canvas.addEventListener("pointerup", marqueeUp);
canvas.addEventListener("pointercancel", marqueeUp);
window.addEventListener("resize", expandCanvas);

start();
