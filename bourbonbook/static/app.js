const photoInput = document.querySelector('[data-photo-input]');
if (photoInput) {
  photoInput.addEventListener('change', async () => {
    const file = photoInput.files?.[0];
    if (!file) return;
    const preview = document.querySelector('img[data-photo-preview]');
    const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif']);
    if (!(preview instanceof HTMLImageElement) || !allowedTypes.has(file.type)) return;
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      try {
        const canvas = document.createElement('canvas');
        const scale = Math.min(1, 1200 / Math.max(bitmap.width, bitmap.height));
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        preview.src = canvas.toDataURL('image/jpeg', 0.9);
      } finally {
        bitmap.close();
      }
      preview.hidden = false;
      document.querySelector('[data-photo-drop]').classList.add('has-photo');
    } catch {
      preview.removeAttribute('src');
      preview.hidden = true;
      document.querySelector('[data-photo-drop]').classList.remove('has-photo');
    }
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

document.querySelectorAll('[data-delete-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm('Remove this bottle permanently?')) event.preventDefault();
  });
});

const editForm = document.querySelector('.edit-form');
const emptyDialog = document.querySelector('[data-empty-dialog]');
let pendingSubmitter = null;
editForm?.addEventListener('submit', (event) => {
  if (event.submitter?.hasAttribute('data-analysis-action')) {
    const overlay = document.querySelector('[data-analysis-overlay]');
    if (overlay) overlay.hidden = false;
    return;
  }
  const status = editForm.querySelector('input[name="status"]:checked')?.value;
  const originalStatus = editForm.querySelector('input[name="original_status"]')?.value;
  const emptyAction = editForm.querySelector('input[name="empty_action"]');
  if (status === 'Empty' && originalStatus !== 'Empty' && !emptyAction?.value && emptyDialog) {
    event.preventDefault();
    pendingSubmitter = event.submitter;
    emptyDialog.showModal();
  }
});

emptyDialog?.querySelectorAll('[data-empty-choice]').forEach((button) => {
  button.addEventListener('click', () => {
    const action = editForm?.querySelector('input[name="empty_action"]');
    if (action) action.value = button.dataset.emptyChoice;
  });
});

emptyDialog?.addEventListener('close', () => {
  if (['shopping', 'remove'].includes(emptyDialog.returnValue)) {
    editForm?.requestSubmit(pendingSubmitter || undefined);
  }
});

if (emptyDialog?.hasAttribute('data-open-on-load')) emptyDialog.showModal();

document.querySelectorAll('input[name="status"]').forEach((input) => {
  input.addEventListener('change', () => {
    if (input.checked && input.value === 'Empty' && fillRange) {
      fillRange.value = '0';
      fillRange.dispatchEvent(new Event('input'));
    }
  });
});

document.querySelector('[data-share-form]')?.addEventListener('submit', (event) => {
  if (
    event.currentTarget.dataset.sharingActive === 'true' &&
    !window.confirm('Replace the current share link? The old link will stop working.')
  ) {
    event.preventDefault();
  }
});

document.querySelector('[data-copy-share-link]')?.addEventListener('click', async (event) => {
  const link = document.querySelector('[data-share-link]');
  if (!link) return;
  await navigator.clipboard.writeText(link.value);
  event.currentTarget.textContent = 'Copied';
});

const dropdowns = [...document.querySelectorAll('[data-dropdown]')];
dropdowns.forEach((dropdown) => {
  dropdown.addEventListener('toggle', () => {
    if (!dropdown.open) return;
    dropdowns.forEach((other) => {
      if (other !== dropdown) other.removeAttribute('open');
    });
  });
});
document.addEventListener('click', (event) => {
  dropdowns.forEach((dropdown) => {
    if (dropdown.open && !dropdown.contains(event.target)) dropdown.removeAttribute('open');
  });
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  const openDropdown = dropdowns.find((dropdown) => dropdown.open);
  if (!openDropdown) return;
  openDropdown.removeAttribute('open');
  openDropdown.querySelector('summary')?.focus();
});

document.querySelector('[data-avatar-input]')?.addEventListener('change', (event) => {
  const file = event.currentTarget.files?.[0];
  const preview = document.querySelector('[data-avatar-preview]');
  if (!file || !(preview instanceof HTMLImageElement)) return;
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  document.querySelector('[data-avatar-fallback]')?.setAttribute('hidden', '');
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js'));
}
