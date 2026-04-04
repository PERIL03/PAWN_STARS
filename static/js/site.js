document.addEventListener("DOMContentLoaded", () => {
  const preloader = document.getElementById("sitePreloader");
  const preloaderShown = window.sessionStorage.getItem("rookhide-preloader-shown") === "1";
  const startedAt = Date.now();

  function revealPage() {
    document.body.classList.remove("preloading");
    document.body.classList.add("page-ready");
  }

  function finishPreloader() {
    if (!preloader) {
      revealPage();
      return;
    }

    preloader.classList.add("is-exiting");
    window.setTimeout(() => {
      preloader.remove();
      revealPage();
    }, 520);
  }

  function schedulePreloaderFinish() {
    const minDuration = preloaderShown ? 280 : 1450;
    const elapsed = Date.now() - startedAt;
    const wait = Math.max(0, minDuration - elapsed);
    window.setTimeout(finishPreloader, wait);
    window.sessionStorage.setItem("rookhide-preloader-shown", "1");
  }

  if (preloader) {
    if (document.readyState === "complete") {
      schedulePreloaderFinish();
    } else {
      window.addEventListener("load", schedulePreloaderFinish, { once: true });
    }
  } else {
    revealPage();
  }

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const navLinks = document.getElementById("siteNavLinks");
  const menuToggle = document.getElementById("menuToggle");

  if (navLinks && menuToggle) {
    menuToggle.addEventListener("click", () => {
      const expanded = menuToggle.getAttribute("aria-expanded") === "true";
      menuToggle.setAttribute("aria-expanded", String(!expanded));
      navLinks.classList.toggle("open", !expanded);
    });

    navLinks.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        navLinks.classList.remove("open");
        menuToggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  const revealTargets = document.querySelectorAll(".reveal");
  if (revealTargets.length) {
    revealTargets.forEach((target, index) => {
      const delayMs = prefersReducedMotion ? 0 : Math.min(420, index * 70);
      target.style.setProperty("--reveal-delay", `${delayMs}ms`);
    });

    if (prefersReducedMotion) {
      revealTargets.forEach((target) => target.classList.add("in-view"));
    } else {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("in-view");
              observer.unobserve(entry.target);
            }
          });
        },
        {
          threshold: 0.18,
          rootMargin: "0px 0px -7% 0px",
        }
      );

      revealTargets.forEach((target) => observer.observe(target));
    }
  }

  if (prefersReducedMotion) {
    return;
  }

  const parallaxNodes = document.querySelectorAll(".parallax");
  if (!parallaxNodes.length) {
    return;
  }

  let ticking = false;
  const applyParallax = () => {
    const y = window.scrollY;
    parallaxNodes.forEach((node) => {
      const speed = Number(node.getAttribute("data-speed") || 0.1);
      node.style.transform = `translate3d(0, ${Math.round(y * speed)}px, 0)`;
    });
    ticking = false;
  };

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        window.requestAnimationFrame(applyParallax);
        ticking = true;
      }
    },
    { passive: true }
  );

  applyParallax();
});
