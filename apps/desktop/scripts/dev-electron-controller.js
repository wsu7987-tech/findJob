export const createElectronDevController = ({
  spawnElectron,
  onElectronExit = () => {},
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout
}) => {
  let electronProcess = null;
  let restartTimer = null;
  let restartingProcess = null;
  let shuttingDown = false;

  const attachExitHandler = (processInstance) => {
    processInstance.once("exit", () => {
      if (electronProcess === processInstance) {
        electronProcess = null;
      }

      if (restartingProcess === processInstance) {
        restartingProcess = null;
        return;
      }

      if (!shuttingDown) {
        onElectronExit();
      }
    });
  };

  const start = () => {
    const processInstance = spawnElectron();
    electronProcess = processInstance;
    attachExitHandler(processInstance);
    return processInstance;
  };

  const restart = () => {
    const previousProcess = electronProcess;
    if (previousProcess) {
      restartingProcess = previousProcess;
      previousProcess.kill();
    }
    return start();
  };

  return {
    start,
    scheduleRestart(reason) {
      if (restartTimer) {
        clearTimeoutFn(restartTimer);
      }

      restartTimer = setTimeoutFn(() => {
        restartTimer = null;
        restart(reason);
      }, 150);
    },
    shutdown() {
      shuttingDown = true;
      if (restartTimer) {
        clearTimeoutFn(restartTimer);
        restartTimer = null;
      }
      if (electronProcess) {
        electronProcess.kill();
        electronProcess = null;
      }
    }
  };
};
