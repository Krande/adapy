import React from 'react';
import { useColorStore } from '../../state/colorLegendStore';

const ColorLegend = () => {
    const { min, max, step, colorPalette } = useColorStore();

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
        <div className="">
            <div className=""/>
        </div>
    );
};

export default ColorLegend;
