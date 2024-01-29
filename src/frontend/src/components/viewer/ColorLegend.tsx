import React from 'react';
import {useColorStore} from '../../state/colorLegendStore';

const ColorLegend = () => {
    const {min, max, step, colorPalette, showLegend} = useColorStore();


    // Convert palette colors to CSS RGB format
    const minColor = `rgb(${colorPalette[0].map(c => c * 255).join(", ")})`;
    const maxColor = `rgb(${colorPalette[1].map(c => c * 255).join(", ")})`;

    const values = [];
    for (let i = 0; i <= step; i++) {
        values.push(min + i * (max - min) / step);
    }

    const gradientStyle = {
        backgroundImage: `linear-gradient(to top, ${minColor}, ${maxColor})`,
    };

    return (
        <div className={showLegend ? "w-20 h-80" : "w-0"} >
            <div className="w-full h-full" style={gradientStyle}/>
        </div>
    );
};

export default ColorLegend;
