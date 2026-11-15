(() => {
  'use strict';

  const controllerStage = document.querySelector('[data-device-stage="controller"]');
  if (!controllerStage) return;

  let lastFormFactor = '';
  let frame = 0;
  function fitController() {
    const mobile = window.matchMedia('(max-width: 760px)').matches;
    const formFactor = mobile ? 'mobile' : 'desktop';
    const baseWidth = mobile ? 320 : 620;
    const baseHeight = mobile ? 320 : 465;
    const rootStyle = getComputedStyle(document.documentElement);
    const safeTop = parseFloat(rootStyle.getPropertyValue('--safe-top')) || 0;
    const safeBottom = parseFloat(rootStyle.getPropertyValue('--safe-bottom')) || 0;
    const marginX = mobile ? 32 : 48;
    const marginY = mobile ? 32 : 48;
    const viewportWidth = window.visualViewport?.width || document.documentElement.clientWidth;
    const viewportHeight = window.visualViewport?.height || document.documentElement.clientHeight;
    const usableWidth = Math.max(1, viewportWidth - marginX);
    const usableHeight = Math.max(1, viewportHeight - safeTop - safeBottom - marginY);
    const scale = Math.min(1, usableWidth / baseWidth, usableHeight / baseHeight);
    const shell = controllerStage.querySelector('[data-device-shell]');
    if (!shell) return;

    if (lastFormFactor !== formFactor) {
      shell.dataset.formFactor = formFactor;
      document.body.classList.toggle('mobile-form', mobile);
      lastFormFactor = formFactor;
      window.dispatchEvent(new CustomEvent('shunyan:form-factor-change', { detail: { formFactor, scale } }));
    }
    shell.style.width = `${baseWidth}px`;
    shell.style.height = `${baseHeight}px`;
    shell.style.transform = `translate(-50%, -50%) scale(${Math.max(.1, scale)})`;
    controllerStage.style.width = `${baseWidth * scale}px`;
    controllerStage.style.height = `${baseHeight * scale}px`;
  }
  function scheduleFit() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(fitController);
  }

  window.addEventListener('resize', scheduleFit, { passive: true });
  window.visualViewport?.addEventListener('resize', scheduleFit, { passive: true });
  fitController();
})();
