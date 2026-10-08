/** One encoder behind the animation export: feed it frames off the shared canvas, get a file. */
export interface FrameSink {
    add(index: number): Promise<void>;
    finish(): Promise<Blob>;
    cancel(): Promise<void>;
}
