const scrollBar = document.getElementById('scrollBar');
function updateScrollBar() {
  const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
  const scrolled = window.scrollY / Math.max(1, scrollHeight);
  scrollBar.style.transform = `scaleX(${scrolled})`;
}

const nav = document.getElementById('nav');
function updateNav() {
  nav.classList.toggle('scrolled', window.scrollY > 60);
  const links = nav.querySelectorAll('.nav-links a');
  let active = null;
  links.forEach(link => {
    const section = document.querySelector(link.getAttribute('href'));
    if (section && section.getBoundingClientRect().top <= window.innerHeight * .4) active = link;
  });
  links.forEach(link => {
    if (link === active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  });
}

const heroText = document.getElementById('heroText');
const heroImage = document.getElementById('heroImage');
const heroSection = document.querySelector('.hero');
function updateHero() {
  const rect = heroSection.getBoundingClientRect();
  const progress = Math.max(0, Math.min(1, -rect.top / (rect.height || 1)));
  if (heroText) heroText.style.transform = `translateY(${-progress * 30}px)`;
  if (heroImage) heroImage.style.transform = `translateY(${progress * 60}px)`;
}

const fadeObs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('in');
      fadeObs.unobserve(e.target);
    }
  });
}, { threshold: 0.15, rootMargin: '0px 0px -60px 0px' });
document.querySelectorAll('.story, .contact, .scene-page, .section-divider').forEach(el => fadeObs.observe(el));

const galleryObs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const idx = Array.from(e.target.parentElement.children).indexOf(e.target);
      e.target.style.transitionDelay = (idx % 4) * 0.08 + 's';
      e.target.classList.add('in');
      galleryObs.unobserve(e.target);
    }
  });
}, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
document.querySelectorAll('.gallery-item').forEach(el => galleryObs.observe(el));

const stepSection = document.getElementById('lookbook');
const dresses = document.querySelectorAll('.step-dress');
const infos = document.querySelectorAll('.step-info');
const dots = document.querySelectorAll('.step-dot');
const stepNum = document.getElementById('stepNum');
const TOTAL = 6;
let currentIndex = 0;

function setActive(idx) {
  if (idx === currentIndex) return;
  currentIndex = idx;
  dresses.forEach((d, i) => d.classList.toggle('active', i === idx));
  infos.forEach((info, i) => info.classList.toggle('active', i === idx));
  dots.forEach((dot, i) => { dot.classList.toggle('active', i === idx); dot.setAttribute('aria-pressed', String(i === idx)); });
  if (stepNum) stepNum.textContent = String(idx + 1).padStart(2, '0');
}

function updateStepScroll() {
  if (!stepSection) return;
  const rect = stepSection.getBoundingClientRect();
  const sectionHeight = stepSection.offsetHeight;
  const viewport = window.innerHeight;
  const scrolled = -rect.top;
  const scrollableDist = sectionHeight - viewport;
  if (scrollableDist <= 0) return;
  const progress = Math.max(0, Math.min(0.999, scrolled / scrollableDist));
  setActive(Math.floor(progress * TOTAL));
}

dots.forEach(dot => {
  dot.addEventListener('click', () => {
    const idx = parseInt(dot.dataset.i);
    const sectionHeight = stepSection.offsetHeight;
    const viewport = window.innerHeight;
    const scrollableDist = sectionHeight - viewport;
    const targetProgress = (idx + 0.5) / TOTAL;
    const targetY = window.scrollY + stepSection.getBoundingClientRect().top + (scrollableDist * targetProgress);
    window.scrollTo({ top: targetY, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  });
});

/* ============ STUDIO scroll-driven deck switcher (B) ============ */
const studioSection = document.getElementById('atelier');
const studioFrames = document.querySelectorAll('.studio-frame');
const studioDotsEls = document.querySelectorAll('.studio-dot');
const studioBigNum = document.getElementById('studioBigNum');
const studioDeckNum = document.getElementById('studioDeckNum');
const studioDeckTitle = document.getElementById('studioDeckTitle');
const studioDeckSub = document.getElementById('studioDeckSub');
const studioProgressFill = document.getElementById('studioProgressFill');
const STUDIO_TOTAL = 4;
let studioCurrent = 0;

const studioMeta = [
  { num: 'N° 01', title: 'The Studio', sub: '门店 · 一次相遇' },
  { num: 'N° 02', title: 'The Selection', sub: '陈列 · 慢慢挑选' },
  { num: 'N° 03', title: 'Your Space', sub: '空间 · 留给自己' },
  { num: 'N° 04', title: 'The Fitting',sub: '试纱 · 珍贵片刻' }
];

function setStudioActive(idx) {
  if (idx === studioCurrent) return;
  studioCurrent = idx;
  studioFrames.forEach((f, i) => {
    f.classList.remove('active', 'passed', 'upcoming-1', 'upcoming-2', 'upcoming-3');
    if (i === idx) f.classList.add('active');
    else if (i < idx) f.classList.add('passed');
    else {
      const offset = i - idx;
      if (offset === 1) f.classList.add('upcoming-1');
      else if (offset === 2) f.classList.add('upcoming-2');
      else f.classList.add('upcoming-3');
    }
  });
  studioDotsEls.forEach((dot, i) => { dot.classList.toggle('active', i === idx); dot.setAttribute('aria-pressed', String(i === idx)); });
  if (studioBigNum) studioBigNum.textContent = String(idx + 1).padStart(2, '0');
  if (studioDeckNum) studioDeckNum.textContent = studioMeta[idx].num;
  if (studioDeckTitle) studioDeckTitle.textContent = studioMeta[idx].title;
  if (studioDeckSub) studioDeckSub.textContent = studioMeta[idx].sub;
  if (studioProgressFill) {
    studioProgressFill.classList.remove('p-1','p-2','p-3','p-4');
    studioProgressFill.classList.add('p-' + (idx + 1));
  }
}

function updateStudioScroll() {
  if (!studioSection) return;
  const rect = studioSection.getBoundingClientRect();
  const sectionHeight = studioSection.offsetHeight;
  const viewport = window.innerHeight;
  const scrollableDist = sectionHeight - viewport;
  if (scrollableDist <= 0) return;
  const inView = rect.top < viewport * 0.6 && rect.bottom > viewport * 0.4;
  studioSection.classList.toggle('in-view', inView);
  const scrolled = -rect.top;
  const progress = Math.max(0, Math.min(0.999, scrolled / scrollableDist));
  setStudioActive(Math.floor(progress * STUDIO_TOTAL));
}

// initialize the deck classes
studioFrames.forEach((f, i) => {
  if (i === 0) f.classList.add('active');
  else if (i === 1) f.classList.add('upcoming-1');
  else if (i === 2) f.classList.add('upcoming-2');
  else f.classList.add('upcoming-3');
});

/* ============ STUDIO 数字 0 → 目标值滚动动效 ============ */
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

function animateStat(el, target, duration, delay) {
  // find the actual text node target (some have a .num-val span inside)
  const valEl = el.querySelector('.num-val') || el;
  const startTime = performance.now() + delay;
  function frame(now) {
    if (now < startTime) {
      // delay phase
      requestAnimationFrame(frame);
      return;
    }
    const elapsed = now - startTime;
    const t = Math.min(1, elapsed / duration);
    const eased = easeOutCubic(t);
    const value = Math.round(target * eased);
    valEl.textContent = value;
    if (t < 1) requestAnimationFrame(frame);
    else valEl.textContent = target;
  }
  requestAnimationFrame(frame);
}

let studioStatsRan = false;
function runStudioStatsCountUp() {
  if (studioStatsRan) return;
  studioStatsRan = true;
  const nums = document.querySelectorAll('.studio-stat-num');
  nums.forEach((el, i) => {
    const target = parseInt(el.dataset.target, 10);
    if (Number.isNaN(target)) return;
    // duration scales a bit with target so 90 doesn't tick frantically vs 3
    const duration = 1100 + Math.min(700, target * 8);
    const delay = 120 + i * 90;
    animateStat(el, target, duration, delay);
  });
}

const statsObs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      runStudioStatsCountUp();
      statsObs.disconnect();
    }
  });
}, { threshold: 0.35 });

const statsContainer = document.querySelector('.studio-stats');
if (statsContainer) statsObs.observe(statsContainer);

studioDotsEls.forEach(dot => {
  dot.addEventListener('click', () => {
    const idx = parseInt(dot.dataset.i);
    const sectionHeight = studioSection.offsetHeight;
    const viewport = window.innerHeight;
    const scrollableDist = sectionHeight - viewport;
    const targetProgress = (idx + 0.5) / STUDIO_TOTAL;
    const targetY = window.scrollY + studioSection.getBoundingClientRect().top + (scrollableDist * targetProgress);
    window.scrollTo({ top: targetY, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  });
});

let ticking = false;
window.addEventListener('scroll', () => {
  if (!ticking) {
    requestAnimationFrame(() => {
      updateScrollBar();
      updateNav();
      updateHero();
      updateStepScroll();
      updateStudioScroll();
      ticking = false;
    });
    ticking = true;
  }
}, { passive: true });

updateStepScroll();
updateStudioScroll();
updateScrollBar();
updateNav();
