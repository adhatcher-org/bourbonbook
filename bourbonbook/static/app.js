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

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js'));
}
