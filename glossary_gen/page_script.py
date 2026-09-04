"""
page_script.py -- the glossary page's client-side JavaScript (search/
filter, and the in-game-screenshot full-page modal viewer), as a plain
string.

Unlike PAGE_CSS, this never goes through .format() (it has no
per-generation placeholders of its own), so it's kept as ordinary JS
with single braces -- no {{ }} escaping needed.
"""

PAGE_SCRIPT = """
const search = document.getElementById('search');
const fileGroups = Array.from(document.querySelectorAll('.file-group'));
const filterInputs = Array.from(document.querySelectorAll('.chip input[data-filter-group]'));
const clearButton = document.getElementById('clear-filters');

function checkedValues(group) {
  return filterInputs
    .filter(el => el.dataset.filterGroup === group && el.checked)
    .map(el => el.value);
}

function applyFilter() {
  const q = search.value.trim().toLowerCase();
  const affSel = checkedValues('affiliation');
  const classSel = checkedValues('class');
  const planetsSel = checkedValues('planets');
  const kindSel = checkedValues('unitkind');

  fileGroups.forEach(g => {
    let anyVisible = false;
    g.querySelectorAll('.row').forEach(r => {
      const nameMatch = !q || r.dataset.name.includes(q);
      const rowAffs = (r.dataset.affiliations || '').split(' ');
      const affMatch = affSel.length === 0 || affSel.some(v => rowAffs.includes(v));
      const rowClasses = (r.dataset.classes || '').split(' ');
      const classMatch = classSel.length === 0 || classSel.some(v => rowClasses.includes(v));
      const planetsMatch = planetsSel.length === 0 || planetsSel.includes(r.dataset.hasPlanets);
      const kindMatch = kindSel.length === 0 || kindSel.includes(r.dataset.unitKind);
      const match = nameMatch && affMatch && classMatch && planetsMatch && kindMatch;
      r.style.display = match ? '' : 'none';
      if (match) anyVisible = true;
    });
    g.style.display = anyVisible ? '' : 'none';
  });
}

search.addEventListener('input', applyFilter);
filterInputs.forEach(el => {
  el.addEventListener('change', () => {
    el.closest('.chip').classList.toggle('active', el.checked);
    applyFilter();
  });
});
if (clearButton) {
  clearButton.addEventListener('click', () => {
    filterInputs.forEach(el => {
      el.checked = false;
      el.closest('.chip').classList.remove('active');
    });
    applyFilter();
  });
}

// Full-page modal viewer for in-game screenshot thumbnails (see
// render_group_row's in_game_images_html) -- ONE shared overlay for
// the whole page (#image-modal in page_template.PAGE_TEMPLATE), not
// one per row, since only a single screenshot is ever being viewed at
// a time regardless of which card's thumbnail opened it. A thumbnail's
// onclick calls openImageModal(src) directly with its own image path.
const imageModal = document.getElementById('image-modal');
const imageModalImg = document.getElementById('image-modal-img');

function openImageModal(src) {
  if (!imageModal || !imageModalImg) return;
  imageModalImg.setAttribute('src', src);
  imageModal.classList.add('open');
}

function closeImageModal() {
  if (!imageModal) return;
  imageModal.classList.remove('open');
  if (imageModalImg) imageModalImg.setAttribute('src', '');
}

if (imageModal) {
  // Clicking anywhere on the dark backdrop closes it; clicking the
  // image itself (or the close button, which stops its own event)
  // must NOT close it, so this listener only fires for a click whose
  // target IS the overlay element, not something inside it.
  imageModal.addEventListener('click', (e) => {
    if (e.target === imageModal) closeImageModal();
  });
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeImageModal();
});
"""
