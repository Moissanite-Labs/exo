// The only stored value is this display preference, never conversation text or credentials.
const KEY = "xc-appearance";
const CHOICES = ["dark", "light", "system"];

export function getAppearance(store) {
  try {
    const value = (store || localStorage).getItem(KEY);
    return CHOICES.includes(value) ? value : "dark";
  } catch (error) { return "dark"; }
}

export function applyAppearance(value, root = document.documentElement) {
  const choice = CHOICES.includes(value) ? value : "dark";
  if (root) root.setAttribute("data-theme", choice);
  return choice;
}

export function setAppearance(value, root = document.documentElement, store) {
  const choice = applyAppearance(value, root);
  try { (store || localStorage).setItem(KEY, choice); } catch (error) { /* unavailable storage: this page keeps the choice */ }
  return choice;
}
