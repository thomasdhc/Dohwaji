"use strict";

const canvas = document.querySelector("#canvas");
const viewport = document.querySelector("#viewport");
const folderName = document.querySelector("#folder-name");
const imageCount = document.querySelector("#image-count");
const saveStatus = document.querySelector("#save-status");
const imageTemplate = document.querySelector("#image-template");
const lightbox = document.querySelector("#lightbox");
const lightboxImage = document.querySelector("#lightbox-image");
const lightboxName = document.querySelector("#lightbox-name");
const openOriginal = document.querySelector("#open-original");
const closeLightboxButton = document.querySelector("#close-lightbox");

const GRID_COLUMNS = 5;
const GRID_X = 300;
const GRID_Y = 270;
const GRID_OFFSET_X = 64;
const GRID_OFFSET_Y = 64;
const DRAG_THRESHOLD = 3;

const items = new Map();
let orderedImages = [];
let highestZ = 0;
let drag = null;
let activeImageIndex = -1;
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

function bringToFront(item) {
  highestZ += 1;
  item.z = highestZ;
  setTilePosition(item);
}

function pointerDown(event, item) {
  if (event.button !== 0 || lightbox.open) return;

  event.preventDefault();
  bringToFront(item);
  drag = {
    item,
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: item.x,
    startY: item.y,
    moved: false,
  };
  item.element.classList.add("dragging");
  item.element.setPointerCapture(event.pointerId);
}

function pointerMove(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;

  const dx = event.clientX - drag.startClientX;
  const dy = event.clientY - drag.startClientY;
  if (Math.hypot(dx, dy) >= DRAG_THRESHOLD) drag.moved = true;

  drag.item.x = Math.max(0, Math.round(drag.startX + dx));
  drag.item.y = Math.max(0, Math.round(drag.startY + dy));
  setTilePosition(drag.item);
  expandCanvas();
}

function pointerUp(event) {
  if (!drag || event.pointerId !== drag.pointerId) return;

  const completed = drag;
  completed.item.element.classList.remove("dragging");
  if (completed.item.element.hasPointerCapture(event.pointerId)) {
    completed.item.element.releasePointerCapture(event.pointerId);
  }
  drag = null;

  if (completed.moved) {
    completed.item.suppressOpenUntil = Date.now() + 500;
  }
  scheduleSave(0);
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

function openLightbox(index) {
  if (index < 0 || index >= orderedImages.length) return;

  activeImageIndex = index;
  const image = orderedImages[index];
  const url = imageUrl(image);
  lightboxImage.src = url;
  lightboxImage.alt = image.name;
  lightboxName.textContent = image.name;
  openOriginal.href = url;

  if (!lightbox.open) lightbox.showModal();
}

function closeLightbox() {
  if (lightbox.open) lightbox.close();
  lightboxImage.removeAttribute("src");
  if (activeImageIndex >= 0) {
    items.get(orderedImages[activeImageIndex].name)?.element.focus({ preventScroll: true });
  }
}

function showAdjacentImage(offset) {
  if (!lightbox.open || orderedImages.length === 0) return;
  activeImageIndex = (activeImageIndex + offset + orderedImages.length) % orderedImages.length;
  openLightbox(activeImageIndex);
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
  element.title = `${image.name}\nDrag to arrange · Double-click to open`;
  element.setAttribute("aria-label", `${image.name}. Drag to arrange; press Enter to open.`);
  element.addEventListener("pointerdown", (event) => pointerDown(event, item));
  element.addEventListener("pointermove", pointerMove);
  element.addEventListener("pointerup", pointerUp);
  element.addEventListener("pointercancel", pointerUp);
  element.addEventListener("dblclick", () => {
    if (Date.now() >= item.suppressOpenUntil) openLightbox(index);
  });
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openLightbox(index);
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
    orderedImages = data.images;
    imageCount.textContent = `${orderedImages.length} image${orderedImages.length === 1 ? "" : "s"}`;

    if (orderedImages.length === 0) {
      showCanvasMessage("This folder has no supported images. Dohwaji currently displays JPEG, PNG, WebP, GIF, and BMP files directly inside the selected folder.");
      setSaveStatus("");
      return;
    }

    let hasNewImages = false;
    orderedImages.forEach((image, index) => {
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

closeLightboxButton.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) closeLightbox();
});
lightbox.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeLightbox();
});
document.addEventListener("keydown", (event) => {
  if (!lightbox.open) return;
  if (event.key === "ArrowLeft") showAdjacentImage(-1);
  if (event.key === "ArrowRight") showAdjacentImage(1);
});
window.addEventListener("resize", expandCanvas);

start();
