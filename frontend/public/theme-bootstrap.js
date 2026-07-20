try {
    document.documentElement.classList.toggle('theme-light', localStorage.getItem('studio_theme') === 'light');
} catch (_) { }
