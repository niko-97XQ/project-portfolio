(() => {
  const section = document.getElementById('bridal-edit');
  const stage = section.querySelector('.bridal-edit-stage');
  const viewport = section.querySelector('.bridal-edit-viewport');
  const track = section.querySelector('.bridal-edit-track');
  const moment = document.getElementById('bridal-moment');
  const momentStage = moment.querySelector('.bridal-moment-stage');
  const picture = moment.querySelector('.bridal-moment-picture');
  const photo = picture.querySelector('img');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let distance = 0, travel = 0, pending = false;
  const clamp = value => Math.max(0, Math.min(1, value));
  document.documentElement.classList.add('editorial-ready');

  function measure() {
    if (reduced.matches) {
      section.style.height = 'auto'; track.style.transform = '';
      section.querySelectorAll('.bridal-edit-image').forEach(image => { image.style.height = ''; });
      picture.style.clipPath = ''; photo.style.transform = '';
      return;
    }
    distance = Math.max(0, track.scrollWidth - viewport.clientWidth);
    travel = Math.max(distance * 1.35, innerHeight * .6);
    section.style.height = `${stage.offsetHeight + travel + 200}px`;
    const captionSpace = Math.max(...[...track.children].map(look =>
      look.querySelector('figcaption').offsetHeight + parseFloat(getComputedStyle(look).paddingTop)
    ));
    const photoHeight = Math.max(100, Math.min(innerWidth < 769 ? 410 : 390, viewport.clientHeight - captionSpace - 2));
    section.querySelectorAll('.bridal-edit-image').forEach(image => { image.style.height = `${photoHeight}px`; });
    paint();
  }
  function paint() {
    pending = false;
    if (reduced.matches) return;
    const rect = section.getBoundingClientRect();
    const progress = clamp((-rect.top - 100) / travel);
    track.style.transform = `translate3d(${-distance * progress}px,0,0)`;
    const mr = moment.getBoundingClientRect();
    // Begin expanding as the image enters; finish while its stage is pinned.
    const reveal = clamp((innerHeight * .7 - mr.top) / (innerHeight * .95));
    const radius = innerWidth < 769 ? 100 : 160;
    picture.style.clipPath = `inset(${7*(1-reveal)}% ${8*(1-reveal)}% round ${radius*(1-reveal)}px ${radius*(1-reveal)}px 0 0)`;
    const movement = clamp(-mr.top / Math.max(1, mr.height - momentStage.offsetHeight));
    photo.style.transform = `translateY(${-5 + movement * 4}%) scale(${1 + movement * .045})`;
  }
  addEventListener('scroll', () => {
    if (!pending) { pending = true; requestAnimationFrame(paint); }
  }, {passive:true});
  addEventListener('resize', measure);
  reduced.addEventListener('change', measure);
  new ResizeObserver(measure).observe(viewport);
  document.fonts.ready.then(measure);
  // Keyboard browsing advances the same vertical timeline, without a second scrollbar.
  viewport.addEventListener('keydown', event => {
    if (reduced.matches || !['ArrowLeft','ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const rect = section.getBoundingClientRect();
    const start = scrollY + rect.top;
    const progress = clamp((-rect.top - 100) / travel);
    const next = clamp(progress + direction * .34);
    scrollTo({top:start + 100 + next * travel, behavior:'smooth'});
  });
  measure();
  const heroTitle = document.querySelector('.hero h1');
  const titleObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      heroTitle.classList.toggle('hero-title-enter', entry.isIntersecting && !reduced.matches);
    });
  }, {threshold:0.2});
  titleObserver.observe(heroTitle);
  reduced.addEventListener('change', () => {
    if (reduced.matches) heroTitle.classList.remove('hero-title-enter');
  });
})();
