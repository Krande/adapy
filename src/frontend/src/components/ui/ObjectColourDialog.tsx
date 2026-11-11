import React, {useState} from "react";

import {colourSelection, NOTHING_RECOLOURED, recolourScope} from "@/utils/scene/objectColorActions";

import {ModalButton, ModalFrame} from "./ModalFrame";

// Edit -> Set colour of selection…: a colour for the selected objects, or for every object when
// nothing is selected (the dialog says which). The colour takes the place of the model's own until
// it is reset; each Apply is one undo step.

/** A few colours that read well against the viewport's background, one click each. */
export const COLOUR_SWATCHES: readonly string[] = ["#d62728", "#ff7f0e", "#f2c200", "#2ca02c", "#17becf", "#1f77b4", "#9467bd", "#8c564b", "#ffffff", "#7f7f7f"];

let lastColour = "#d62728";

const ObjectColourDialog: React.FC<{onClose: () => void}> = ({onClose}) => {
    const [colour, setColour] = useState(lastColour);
    const [note, setNote] = useState<string | null>(null);
    const scope = recolourScope();
    const apply = (value: string | null) => {
        if (value) lastColour = value;
        // Said, not silently closed: the user asked for a colour and sees none.
        if (colourSelection(value) === 0 && value !== null) return setNote(NOTHING_RECOLOURED);
        onClose();
    };
    return (
        <ModalFrame
            title="Set colour"
            onClose={onClose}
            testId="object-colour-dialog"
            footer={
                <>
                    <ModalButton onClick={onClose}>Cancel</ModalButton>
                    <ModalButton onClick={() => apply(null)}>Model's own colour</ModalButton>
                    <ModalButton primary onClick={() => apply(colour)}>Apply</ModalButton>
                </>
            }
        >
            <div className="space-y-3">
                <p data-testid="object-colour-scope">{scope.text}</p>
                <label className="flex items-center gap-3">
                    <span>Colour</span>
                    <input
                        type="color"
                        data-testid="object-colour-input"
                        value={colour}
                        onChange={(e) => setColour(e.target.value)}
                        className="h-7 w-12 cursor-pointer rounded-sm border border-gray-600 bg-transparent"
                    />
                    <span className="font-mono text-xs text-gray-300">{colour}</span>
                </label>
                <div className="flex flex-wrap gap-1.5">
                    {COLOUR_SWATCHES.map((c) => (
                        <button
                            key={c}
                            type="button"
                            title={c}
                            aria-label={`Colour ${c}`}
                            onClick={() => setColour(c)}
                            className={"h-6 w-6 cursor-pointer rounded-sm border " + (c === colour ? "border-blue-400 ring-1 ring-blue-400" : "border-gray-600")}
                            style={{background: c}}
                        />
                    ))}
                </div>
                <p className="text-xs text-gray-400">
                    The colour takes the place of the model's own, and is lit like it. Hiding, isolating and section planes keep it.
                    Undo takes it back, and "Reset colours" gives every object its own colour again.
                </p>
                {note && <p className="text-xs text-amber-300" data-testid="object-colour-note">{note}</p>}
            </div>
        </ModalFrame>
    );
};

export default ObjectColourDialog;
