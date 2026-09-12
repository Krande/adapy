/**
 * Facade for the cellbuilder store.
 *
 * The store used to live here as one file; it is now a package of zustand
 * slices under ./cellbuilder, composed into the SAME single store with the same
 * flat shape. This module stays as the stable import path
 * (`@/state/cellBuilderStore`) so every component, helper and test is unchanged
 * — start at ./cellbuilder/index.ts to find the slice that owns a field.
 */

export * from "./cellbuilder";
