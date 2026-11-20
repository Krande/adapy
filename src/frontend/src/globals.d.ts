/// <reference types="vite/client" />

// allow `import X from "foo.worker.ts?worker"`
declare module "*?worker" {
  const WorkerFactory: new () => Worker;
  export default WorkerFactory;
}
