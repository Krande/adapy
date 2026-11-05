// `position: fixed` inside a transformed ancestor is fixed to that ancestor, not
// to the viewport -- and a floating window (`react-rnd`) positions itself with a
// transform. So a full-screen dialog or a menu drawn by a panel that can float
// would open inside the window instead of over the page.
//
// `OverlayPortal` draws its children into `document.body` when they are inside
// such a window (`TransformedHost`), and in place otherwise, so a docked panel
// renders exactly as it did.

import React, {createContext, useContext} from "react";
import {createPortal} from "react-dom";

const TransformedHostContext = createContext(false);

/** Marks content drawn inside a transformed container, such as a floating window. */
export const TransformedHost: React.FC<{children: React.ReactNode}> = ({children}) => (
  <TransformedHostContext.Provider value={true}>{children}</TransformedHostContext.Provider>
);

/** Is this content inside a `TransformedHost`. */
export function useInTransformedHost(): boolean {
  return useContext(TransformedHostContext);
}

/** A fixed overlay's children: portalled to the body inside a `TransformedHost`, in place elsewhere. */
export function OverlayPortal({children}: {children: React.ReactNode}) {
  const transformed = useInTransformedHost();
  if (!transformed || typeof document === "undefined") return <>{children}</>;
  return createPortal(children, document.body);
}
