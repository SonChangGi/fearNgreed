export function itemRatioAt(items, index, ratioForItem = null) {
  if (!items.length) return 0;
  if (typeof ratioForItem === "function") {
    const ratio = Number(ratioForItem(items[index], index));
    if (Number.isFinite(ratio)) return Math.max(0, Math.min(1, ratio));
  }
  return items.length <= 1 ? 1 : index / (items.length - 1);
}

export function nearestItemIndexByRatio(items, targetRatio, ratioForItem = null) {
  if (!items.length) return -1;
  const target = Math.max(0, Math.min(1, Number(targetRatio) || 0));
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  items.forEach((item, index) => {
    const distance = Math.abs(itemRatioAt(items, index, ratioForItem) - target);
    if (distance < bestDistance) {
      bestIndex = index;
      bestDistance = distance;
    }
  });
  return bestIndex;
}

function finitePoint(point) {
  return point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))
    ? { x: Number(point.x), y: Number(point.y) }
    : null;
}

function transformPoint(svg, x, y, matrix) {
  if (typeof svg?.createSVGPoint === "function") {
    try {
      const point = svg.createSVGPoint();
      point.x = x;
      point.y = y;
      const transformed = typeof point.matrixTransform === "function"
        ? finitePoint(point.matrixTransform(matrix))
        : null;
      if (transformed) return transformed;
    } catch (_) {
      // Continue to the next available matrix implementation.
    }
  }
  if (typeof globalThis.DOMPoint === "function") {
    try {
      const transformed = finitePoint(new globalThis.DOMPoint(x, y).matrixTransform(matrix));
      if (transformed) return transformed;
    } catch (_) {
      // Continue to DOMMatrix.transformPoint when available.
    }
  }
  if (typeof matrix?.transformPoint === "function") {
    try {
      return finitePoint(matrix.transformPoint({ x, y }));
    } catch (_) {
      // Detached SVGs and lightweight DOM test runtimes can expose partial matrix APIs.
    }
  }
  return null;
}

function viewBoxFor(svg, override = null) {
  const value = override || svg?.viewBox?.baseVal;
  const width = Number(value?.width);
  const height = Number(value?.height);
  if (!(width > 0) || !(height > 0)) return null;
  return {
    x: Number(value?.x) || 0,
    y: Number(value?.y) || 0,
    width,
    height
  };
}

function fallbackViewport(svg, override = null) {
  const viewBox = viewBoxFor(svg, override);
  const rect = svg?.getBoundingClientRect?.();
  if (!viewBox || !(Number(rect?.width) > 0) || !(Number(rect?.height) > 0)) return null;
  const preserve = String(svg?.getAttribute?.("preserveAspectRatio") || "xMidYMid meet").trim();
  if (/\bnone\b/.test(preserve)) {
    return {
      viewBox,
      rect,
      scaleX: rect.width / viewBox.width,
      scaleY: rect.height / viewBox.height,
      offsetX: 0,
      offsetY: 0
    };
  }
  const slice = /\bslice\b/.test(preserve);
  const scale = slice
    ? Math.max(rect.width / viewBox.width, rect.height / viewBox.height)
    : Math.min(rect.width / viewBox.width, rect.height / viewBox.height);
  const renderedWidth = viewBox.width * scale;
  const renderedHeight = viewBox.height * scale;
  const xFactor = /\bxMin/.test(preserve) ? 0 : /\bxMax/.test(preserve) ? 1 : 0.5;
  const yFactor = /\bYMin/.test(preserve) ? 0 : /\bYMax/.test(preserve) ? 1 : 0.5;
  return {
    viewBox,
    rect,
    scaleX: scale,
    scaleY: scale,
    offsetX: (rect.width - renderedWidth) * xFactor,
    offsetY: (rect.height - renderedHeight) * yFactor
  };
}

export function clientPointToSvg(svg, clientX, clientY, override = null) {
  try {
    const matrix = svg?.getScreenCTM?.();
    const inverse = matrix?.inverse?.();
    const transformed = inverse ? transformPoint(svg, clientX, clientY, inverse) : null;
    if (transformed) return transformed;
  } catch (_) {
    // Fall back to preserveAspectRatio-aware viewport math below.
  }
  const viewport = fallbackViewport(svg, override);
  if (!viewport) return null;
  return {
    x: viewport.viewBox.x + (clientX - viewport.rect.left - viewport.offsetX) / viewport.scaleX,
    y: viewport.viewBox.y + (clientY - viewport.rect.top - viewport.offsetY) / viewport.scaleY
  };
}

export function svgPointToClient(svg, svgX, svgY, override = null) {
  try {
    const matrix = svg?.getScreenCTM?.();
    const transformed = matrix ? transformPoint(svg, svgX, svgY, matrix) : null;
    if (transformed) return transformed;
  } catch (_) {
    // Fall back to preserveAspectRatio-aware viewport math below.
  }
  const viewport = fallbackViewport(svg, override);
  if (!viewport) return null;
  return {
    x: viewport.rect.left + viewport.offsetX + (svgX - viewport.viewBox.x) * viewport.scaleX,
    y: viewport.rect.top + viewport.offsetY + (svgY - viewport.viewBox.y) * viewport.scaleY
  };
}
