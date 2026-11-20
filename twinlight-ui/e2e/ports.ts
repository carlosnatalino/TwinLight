// Where the browser tests run the UI and the twins. Ports away from the
// defaults (5173, 8080, 50051), so a running development stack is untouched.
// 127.0.0.1 rather than localhost: the twin's CORS check compares the origin
// as the browser writes it, and localhost may resolve to ::1 first.

export const UI_PORT = 15173;
export const UI_ORIGIN = `http://127.0.0.1:${UI_PORT}`;

export type TwinName = "mesh" | "meshAuth" | "coronet";

interface TwinSpec {
  config: string;
  restPort: number;
  grpcPort: number;
  /** True when the twin requires the run's API token. */
  auth: boolean;
  url: string;
}

function twin(config: string, restPort: number, grpcPort: number, auth: boolean): TwinSpec {
  return { config, restPort, grpcPort, auth, url: `http://127.0.0.1:${restPort}` };
}

export const TWINS: Record<TwinName, TwinSpec> = {
  mesh: twin("mesh.yaml", 18080, 15051, false),
  meshAuth: twin("mesh.yaml", 18082, 15053, true),
  coronet: twin("coronet.yaml", 18081, 15052, false),
};
