import * as React from "react"

// Below this width the app treats the viewport as mobile (shadcn sidebar
// collapse behavior). Was workbench/breakpoints.js in the CAD donor; the
// the board workspace only needs the single threshold.
export const MOBILE_BREAKPOINT_PX = 520;
const MOBILE_MEDIA_QUERY = `(max-width: ${MOBILE_BREAKPOINT_PX - 1}px)`;

function isMobileViewport(width) {
  const numericWidth = Number(width);
  if (!Number.isFinite(numericWidth)) {
    return false;
  }
  return numericWidth < MOBILE_BREAKPOINT_PX;
}

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState(() => (
    typeof window === "undefined" ? false : isMobileViewport(window.innerWidth)
  ))

  React.useEffect(() => {
    const mql = window.matchMedia(MOBILE_MEDIA_QUERY)
    const onChange = () => {
      setIsMobile(isMobileViewport(window.innerWidth))
    }
    mql.addEventListener("change", onChange)
    setIsMobile(isMobileViewport(window.innerWidth))
    return () => mql.removeEventListener("change", onChange);
  }, [])

  return !!isMobile
}
