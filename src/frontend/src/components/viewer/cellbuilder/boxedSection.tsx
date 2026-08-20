import React from "react";
import {CollapsibleSection} from "@/components/ui";

// The cellbuilder's disclosure: the design system's CollapsibleSection, pinned to the
// variant and default this panel has always used.
//
// Boxed because these groups sit among ordinary tool rows and the outline is what says
// "container, not another row". Closed because the panel is a floating 340px column and
// every group opened by default is a group you have to scroll past — the point of the
// disclosures is that the panel opens compact.
//
// A thin wrapper rather than 5 call sites repeating `variant="boxed" defaultOpen={false}`.
export const Section: React.FC<{
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}> = ({title, count, defaultOpen = false, children}) => (
  <CollapsibleSection title={title} count={count} defaultOpen={defaultOpen} variant="boxed">
    {children}
  </CollapsibleSection>
);
