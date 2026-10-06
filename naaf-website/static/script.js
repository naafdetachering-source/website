document.addEventListener('DOMContentLoaded', () => {
  const menuToggle = document.querySelector('.menu-toggle');
  const navLinks = document.querySelector('nav.links');

  if (menuToggle && navLinks) {
    const header = document.querySelector('header.site');
    const setMenu = (open) => {
      header.classList.toggle('menu-open', open);
      menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      menuToggle.setAttribute('aria-label', open ? 'Menu sluiten' : 'Menu openen');
    };
    menuToggle.addEventListener('click', () => {
      setMenu(!header.classList.contains('menu-open'));
    });
    // Sluit het menu zodra je op een link klikt (bijv. #faq), met Escape, of bij terug naar desktopbreedte
    navLinks.querySelectorAll('a').forEach((a) => a.addEventListener('click', () => setMenu(false)));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });
    window.addEventListener('resize', () => { if (window.innerWidth > 960) setMenu(false); });
  }

  // Only one FAQ item open at a time
  document.querySelectorAll('.faq-item').forEach((item) => {
    item.addEventListener('toggle', () => {
      if (item.open) {
        document.querySelectorAll('.faq-item').forEach((other) => {
          if (other !== item) other.open = false;
        });
      }
    });
  });

  // If a form submission redirected back with a flash message, scroll it into view
  const flash = document.querySelector('.flash');
  if (flash) {
    flash.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // Limit "functie(s)" selection to the configured maximum (e.g. 3)
  document.querySelectorAll('.functie-list').forEach((list) => {
    const max = parseInt(list.dataset.max, 10) || 3;
    const checkboxes = Array.from(list.querySelectorAll('.functie-checkbox'));

    const updateState = () => {
      const checkedCount = checkboxes.filter((cb) => cb.checked).length;
      checkboxes.forEach((cb) => {
        const option = cb.closest('.functie-option');
        if (!cb.checked && checkedCount >= max) {
          cb.disabled = true;
          option.classList.add('disabled');
        } else {
          cb.disabled = false;
          option.classList.remove('disabled');
        }
      });
    };

    checkboxes.forEach((cb) => cb.addEventListener('change', updateState));
    updateState();
  });

  // Tag-input widget: type + Enter/comma to add a tag pill, click x to remove.
  // Keeps a hidden, comma-separated field in sync for the server to read.
  document.querySelectorAll('.tag-input-box').forEach((box) => {
    const input = box.querySelector('.tag-input-field');
    const hidden = document.getElementById(input.id.replace('-field', '-hidden'));
    let tags = [];

    const syncHidden = () => {
      hidden.value = tags.join(',');
    };

    const renderTags = () => {
      box.querySelectorAll('.tag-pill').forEach((el) => el.remove());
      tags.forEach((tag, index) => {
        const pill = document.createElement('span');
        pill.className = 'tag-pill';
        const label = document.createElement('span');
        label.textContent = tag;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Verwijder tag');
        remove.textContent = '×';
        remove.addEventListener('click', () => {
          tags.splice(index, 1);
          renderTags();
          syncHidden();
        });
        pill.appendChild(label);
        pill.appendChild(remove);
        box.insertBefore(pill, input);
      });
    };

    const addTag = (value) => {
      const clean = value.trim();
      if (clean && !tags.includes(clean) && tags.length < 30) {
        tags.push(clean);
        renderTags();
        syncHidden();
      }
      input.value = '';
    };

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addTag(input.value);
      } else if (event.key === 'Backspace' && input.value === '' && tags.length > 0) {
        tags.pop();
        renderTags();
        syncHidden();
      }
    });

    input.addEventListener('blur', () => {
      if (input.value.trim()) addTag(input.value);
    });

    box.addEventListener('click', () => input.focus());
  });
});
