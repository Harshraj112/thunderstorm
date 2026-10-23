// Stand-in for next/link used only by the esbuild harness bundle (pramaan/web_dist). Same props, client-side nav.
import { navigate } from "./next-navigation";

export default function Link({ href, children, onClick, ...rest }: any) {
  return (
    <a
      href={href}
      onClick={(e) => {
        onClick?.(e);
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || rest.target) return;
        e.preventDefault();
        navigate(href);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
