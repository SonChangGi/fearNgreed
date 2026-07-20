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
