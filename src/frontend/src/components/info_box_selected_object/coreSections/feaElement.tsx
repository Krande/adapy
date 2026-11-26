// The "fea-element" and "fea-sets" sections: a picked FEA element's own data, each only in the
// flat layout (the classic panel shows the same folded under Properties).

import React from "react";
import FeaElementSection from "../FeaElementSection";
import FeaSetsSection from "../FeaSetsSection";
import {ObjectInfoOptions} from "../objectInfoOptions";

/** The picked FEA element in the flat layout; nothing in the classic panel, which shows it folded. */
export const FlatFeaElement: React.FC<{name: string | null}> = ({name}) => {
  const {flat} = React.useContext(ObjectInfoOptions);
  return flat ? <FeaElementSection name={name} /> : null;
};

/** The FEA sets section, in the flat layout's style when the panel is flat. */
export const FeaSets: React.FC = () => <FeaSetsSection flat={!!React.useContext(ObjectInfoOptions).flat} />;
