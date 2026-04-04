(function () {
  function getFramer() {
    return window["framer-motion"] || window.framerMotion || window.Motion || null;
  }

  function mountReact(node, component) {
    if (!node || !window.React || !window.ReactDOM) {
      return;
    }

    if (typeof window.ReactDOM.createRoot === "function") {
      const root = window.ReactDOM.createRoot(node);
      root.render(component);
      return;
    }

    if (typeof window.ReactDOM.render === "function") {
      window.ReactDOM.render(component, node);
    }
  }

  function initHomeShowcase() {
    const mountNode = document.getElementById("framerHeroMount");
    const framer = getFramer();
    if (!mountNode || !framer || !framer.motion || !window.React) {
      return;
    }

    const React = window.React;
    const h = React.createElement;
    const motion = framer.motion;

    function HomeShowcase() {
      return h(
        "div",
        { className: "fm-home-grid" },
        h(
          motion.div,
          {
            className: "fm-floating-card fm-card-a",
            initial: { opacity: 0, y: 30, rotate: -6 },
            animate: { opacity: 1, y: 0, rotate: -2 },
            transition: { duration: 0.9, ease: "easeOut" },
          },
          h("span", { className: "fm-pill" }, "Stealth Layer"),
          h("strong", null, "Byte stream -> tournament-grade PGN"),
          h("p", null, "Looks like chess. Carries secure payload.")
        ),
        h(
          motion.div,
          {
            className: "fm-floating-card fm-card-b",
            initial: { opacity: 0, y: 25, rotate: 7 },
            animate: { opacity: 1, y: 0, rotate: 3 },
            transition: { delay: 0.15, duration: 0.9, ease: "easeOut" },
          },
          h("span", { className: "fm-pill" }, "Rust Core"),
          h("strong", null, "Engine speed with deterministic decode"),
          h("p", null, "High-throughput transform for live demos.")
        ),
        h(
          motion.div,
          {
            className: "fm-floating-card fm-card-c",
            initial: { opacity: 0, scale: 0.8 },
            animate: { opacity: 1, scale: 1 },
            transition: { delay: 0.3, duration: 0.7, ease: "easeOut" },
          },
          h("span", { className: "fm-big-number" }, "232.9x"),
          h("small", null, "avg Rust speedup vs Python")
        )
      );
    }

    mountReact(mountNode, h(HomeShowcase));
  }

  function initUploadShowcase() {
    const mountNode = document.getElementById("framerUploadMount");
    const framer = getFramer();
    if (!mountNode || !framer || !framer.motion || !window.React) {
      return;
    }

    const React = window.React;
    const h = React.createElement;
    const motion = framer.motion;
    const useEffect = React.useEffect;
    const useRef = React.useRef;
    const useState = React.useState;

    const steps = [
      "Import File",
      "Compress + Encrypt",
      "Encode As PGN",
      "Export Ready"
    ];

    function UploadShowcase() {
      const [activeStep, setActiveStep] = useState(-1);
      const [tourRunning, setTourRunning] = useState(false);
      const tourTimerRef = useRef(null);

      useEffect(function () {
        return function cleanup() {
          if (tourTimerRef.current) {
            window.clearInterval(tourTimerRef.current);
            tourTimerRef.current = null;
          }
        };
      }, []);

      function runDemoTour() {
        if (tourTimerRef.current) {
          window.clearInterval(tourTimerRef.current);
          tourTimerRef.current = null;
        }

        setTourRunning(true);
        setActiveStep(0);
        let cursor = 0;

        tourTimerRef.current = window.setInterval(function () {
          cursor += 1;
          if (cursor >= steps.length) {
            window.clearInterval(tourTimerRef.current);
            tourTimerRef.current = null;
            setTourRunning(false);
            window.setTimeout(function () {
              setActiveStep(-1);
            }, 550);
            return;
          }
          setActiveStep(cursor);
        }, 900);
      }

      return h(
        motion.div,
        {
          className: "fm-upload-shell",
          initial: { opacity: 0, y: 18 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.55, ease: "easeOut" },
        },
        h("div", { className: "fm-upload-headline" },
          h("h3", null, "Presentation Mode"),
          h("p", null, "Four-step workflow overview."),
          h(
            "button",
            {
              type: "button",
              className: "fm-tour-btn",
              onClick: runDemoTour,
              disabled: tourRunning,
            },
            tourRunning ? "Tour in progress..." : "Start Tour"
          )
        ),
        h(
          "div",
          {
            className: "fm-steps",
            "aria-live": "polite",
          },
          steps.map(function (step, index) {
            const isActive = activeStep === index;
            return h(
              motion.div,
              {
                key: step,
                className: "fm-step-chip" + (isActive ? " tour-focus" : ""),
                initial: { opacity: 0, y: 12, scale: 0.95 },
                animate: {
                  opacity: 1,
                  y: 0,
                  scale: isActive ? 1.06 : 1,
                  boxShadow: isActive
                    ? "0 0 0 2px rgba(255, 207, 146, 0.9), 0 14px 28px rgba(0,0,0,0.34)"
                    : "0 8px 18px rgba(0,0,0,0.2)",
                },
                transition: { delay: 0.08 * index, duration: 0.4 },
                whileHover: { y: -3, scale: 1.03 },
              },
              h("span", { className: "fm-step-index" }, String(index + 1).padStart(2, "0")),
              h("span", null, step)
            );
          })
        )
      );
    }

    mountReact(mountNode, h(UploadShowcase));
  }

  window.addEventListener("DOMContentLoaded", function () {
    initHomeShowcase();
    initUploadShowcase();
  });
})();
