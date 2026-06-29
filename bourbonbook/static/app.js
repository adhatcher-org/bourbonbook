const photoInput = document.querySelector('[data-photo-input]');
if (photoInput) {
  photoInput.addEventListener('change', () => {
    const file = photoInput.files?.[0];
    if (!file) return;
    const preview = document.querySelector('[data-photo-preview]');
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
    document.querySelector('[data-photo-drop]').classList.add('has-photo');
  });
}

const uploadForm = document.querySelector('[data-upload-form]');
if (uploadForm) {
  uploadForm.addEventListener('submit', () => {
    document.querySelector('[data-analyzing]').hidden = false;
    document.querySelector('[data-submit-button]').disabled = true;
  });
}

const fillRange = document.querySelector('[data-fill-range]');
if (fillRange) {
  const output = document.querySelector('[data-fill-output]');
  const updateFill = () => {
    output.value = `${fillRange.value}%`;
    fillRange.style.setProperty('--range-progress', `${fillRange.value}%`);
  };
  fillRange.addEventListener('input', updateFill);
  updateFill();
}

document.querySelector('[data-delete-form]')?.addEventListener('submit', (event) => {
  if (!window.confirm('Delete this bottle permanently?')) event.preventDefault();
});

document.querySelector('.edit-form')?.addEventListener('submit', (event) => {
  if (event.submitter?.hasAttribute('data-analysis-action')) {
    const overlay = document.querySelector('[data-analysis-overlay]');
    if (overlay) overlay.hidden = false;
  }
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js'));
}
