import React from 'react';

const CopyIcon = (props: React.SVGProps<SVGSVGElement>) => (
  <svg
    {...props}
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
    stroke="currentColor"
    className="w-6 h-6"
  >
    {/* Bottom document */}
    <rect
      x={4}
      y={4}
      width={12}
      height={16}
      rx={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    {/* Top document */}
    <rect
      x={8}
      y={8}
      width={12}
      height={16}
      rx={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

export default CopyIcon;
