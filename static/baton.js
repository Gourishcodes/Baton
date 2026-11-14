// Baton keyboard navigation and drawer centering
function scrollCitedToCenter() {
  const d = document.getElementById('drawer'), c = d?.querySelector('.srcline--cited');
  if (d && c) {
    const h = d.querySelector('header')?.offsetHeight || 0;
    d.scrollTop += c.getBoundingClientRect().top - (d.getBoundingClientRect().top + h) - ((d.clientHeight - h) / 2) + (c.clientHeight / 2);
  }
}
document.addEventListener('DOMContentLoaded', scrollCitedToCenter);
window.addEventListener('load', scrollCitedToCenter);
document.addEventListener('htmx:afterSwap', () => setTimeout(scrollCitedToCenter, 10));

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.role !== 'lead') return;
  let selectedIndex = 0;
  const getClaims = () => Array.from(document.querySelectorAll('.ledger .claim:not(.claim--redacted)'));

  function selectClaim(index) {
    const claims = getClaims();
    if (!claims.length) return;
    selectedIndex = Math.max(0, Math.min(index, claims.length - 1));
    claims.forEach((c, i) => {
      c.setAttribute('aria-selected', i === selectedIndex ? 'true' : 'false');
      if (i === selectedIndex) c.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  }

  document.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    const claims = getClaims();
    if (!claims.length) return;
    const current = claims[selectedIndex];
    if (e.key === 'j' || e.key === 'J') {
      selectClaim(selectedIndex + 1);
    } else if (e.key === 'k' || e.key === 'K') {
      selectClaim(selectedIndex - 1);
    } else if (e.key === 'a' || e.key === 'A') {
      if (current?.querySelector('.status--pending')) {
        current.querySelector('.acts .btn--primary')?.click();
      }
    } else if (e.key === 'e' || e.key === 'E') {
      current?.querySelector('.acts button:nth-child(2)')?.click();
    } else if (e.key === 'r' || e.key === 'R') {
      current?.querySelector('.acts .btn--danger, .acts .btn--quiet')?.click();
    } else if (e.key === 'Enter') {
      current?.querySelector('.cite')?.click();
    }
  });

  selectClaim(0);
});
