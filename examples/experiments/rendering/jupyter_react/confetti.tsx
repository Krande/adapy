// confetti.tsx
import confetti from "canvas-confetti";
import * as React from "react";


export default function({value, set_value, debug}) {
    return <button onClick={() => confetti() && set_value(value + 1)}>
        {value || 0} times confetti
    </button>
};