/**
 * Just enough DOM for Cytoscape's canvas renderer under jsdom.
 *
 * jsdom has no canvas (getContext returns null, and Cytoscape refuses to
 * start), lays nothing out (every element is 0 x 0) and leaves padding
 * undefined in computed styles (Cytoscape reads it to size the viewport). With
 * these stubs a real core renders into no-op 2-D contexts, so its layouts,
 * animation frames and destroy() run as they do in a browser.
 */

const WIDTH = 800;
const HEIGHT = 600;

function fakeContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const noop = () => undefined;
  return new Proxy({} as CanvasRenderingContext2D, {
    get(_target, key) {
      switch (key) {
        case "canvas":
          return canvas;
        case "measureText":
          return () => ({ width: 10, actualBoundingBoxAscent: 5, actualBoundingBoxDescent: 5 });
        case "getImageData":
          return () => ({ data: new Uint8ClampedArray(4) });
        case "createLinearGradient":
        case "createRadialGradient":
        case "createPattern":
          return () => ({ addColorStop: noop });
        case "getTransform":
          return () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
        default:
          return noop;
      }
    },
    set: () => true,
  });
}

/** Install the stubs; returns a function that removes them. */
export function installCytoscapeDom(): () => void {
  const proto = HTMLCanvasElement.prototype;
  const getContext = Object.getOwnPropertyDescriptor(proto, "getContext");
  const width = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
  const height = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");

  Object.defineProperty(proto, "getContext", {
    configurable: true,
    writable: true,
    value: function (this: HTMLCanvasElement) {
      return fakeContext(this);
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => WIDTH,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => HEIGHT,
  });
  const style = document.createElement("style");
  style.textContent = "div { padding: 0px; }";
  document.head.appendChild(style);

  const restore = (target: object, key: string, saved: PropertyDescriptor | undefined) => {
    if (saved) Object.defineProperty(target, key, saved);
    else delete (target as Record<string, unknown>)[key];
  };
  return () => {
    restore(proto, "getContext", getContext);
    restore(HTMLElement.prototype, "clientWidth", width);
    restore(HTMLElement.prototype, "clientHeight", height);
    style.remove();
  };
}

/**
 * Collect uncaught errors thrown outside React, such as in an animation frame
 * callback, which is where a layout touching a destroyed core throws.
 */
export function collectUncaughtErrors(): { errors: string[]; stop: () => void } {
  const errors: string[] = [];
  const onError = (event: ErrorEvent) => {
    const err = event.error as Error | undefined;
    errors.push(err?.stack ?? String(event.message));
    event.preventDefault();
  };
  window.addEventListener("error", onError);
  return { errors, stop: () => window.removeEventListener("error", onError) };
}

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
