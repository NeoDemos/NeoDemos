// NeoDemos — main JS entry point (bundled by Vite)
import '../css/main.css';

document.addEventListener('DOMContentLoaded', () => {
  // Mobile nav toggle
  const hamburger = document.getElementById('nav-hamburger');
  const mobileNav = document.getElementById('nav-mobile');
  const overlay = document.getElementById('nav-overlay');

  if (hamburger && mobileNav && overlay) {
    hamburger.addEventListener('click', () => {
      mobileNav.classList.toggle('open');
      overlay.classList.toggle('open');
    });

    overlay.addEventListener('click', () => {
      mobileNav.classList.remove('open');
      overlay.classList.remove('open');
    });
  }
});
