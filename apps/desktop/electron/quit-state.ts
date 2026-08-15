export const createQuitState = () => {
  let isQuitting = false;

  return {
    beginQuit() {
      isQuitting = true;
    },
    shouldHideMainWindowOnClose(closeToTray: boolean) {
      return closeToTray && !isQuitting;
    }
  };
};
