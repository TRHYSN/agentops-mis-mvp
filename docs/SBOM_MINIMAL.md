# Minimal SBOM

This is a lightweight, human-readable software bill of materials for the local
AgentOps MIS MVP. Exact transitive npm versions are recorded in
`ui/start-building-app/package-lock.json`; exact Python package metadata is
recorded in `pyproject.toml`.

## First-Party Components

| Component | Version | Location | License posture |
|---|---:|---|---|
| agentops-mis-cli | 0.1.0 | `pyproject.toml`, `agentops_mis_cli/` | Proprietary local MVP |
| agentops-mis-backend | local MVP | `server.py` | Proprietary local MVP |
| agentops-mis-ui | 0.0.1 | `ui/start-building-app/` | UNLICENSED/private |
| pixel-office-visualizer | local MVP | `assets/pixel-office/`, `ui/start-building-app/src/app/components/pixel/` | Project-owned source-rendered pack, no copied third-party art |

## Python Direct Dependencies

The installable CLI package currently declares no direct Python package
dependencies in `pyproject.toml`.

## npm Direct Dependencies

| Package | Version |
|---|---:|
| @emotion/react | 11.14.0 |
| @emotion/styled | 11.14.1 |
| @mui/icons-material | 7.3.5 |
| @mui/material | 7.3.5 |
| @popperjs/core | 2.11.8 |
| @radix-ui/react-accordion | 1.2.3 |
| @radix-ui/react-alert-dialog | 1.1.6 |
| @radix-ui/react-aspect-ratio | 1.1.2 |
| @radix-ui/react-avatar | 1.1.3 |
| @radix-ui/react-checkbox | 1.1.4 |
| @radix-ui/react-collapsible | 1.1.3 |
| @radix-ui/react-context-menu | 2.2.6 |
| @radix-ui/react-dialog | 1.1.6 |
| @radix-ui/react-dropdown-menu | 2.1.6 |
| @radix-ui/react-hover-card | 1.1.6 |
| @radix-ui/react-label | 2.1.2 |
| @radix-ui/react-menubar | 1.1.6 |
| @radix-ui/react-navigation-menu | 1.2.5 |
| @radix-ui/react-popover | 1.1.6 |
| @radix-ui/react-progress | 1.1.2 |
| @radix-ui/react-radio-group | 1.2.3 |
| @radix-ui/react-scroll-area | 1.2.3 |
| @radix-ui/react-select | 2.1.6 |
| @radix-ui/react-separator | 1.1.2 |
| @radix-ui/react-slider | 1.2.3 |
| @radix-ui/react-slot | 1.1.2 |
| @radix-ui/react-switch | 1.1.3 |
| @radix-ui/react-tabs | 1.1.3 |
| @radix-ui/react-toggle | 1.1.2 |
| @radix-ui/react-toggle-group | 1.1.2 |
| @radix-ui/react-tooltip | 1.1.8 |
| canvas-confetti | 1.9.4 |
| class-variance-authority | 0.7.1 |
| clsx | 2.1.1 |
| cmdk | 1.1.1 |
| date-fns | 3.6.0 |
| embla-carousel-react | 8.6.0 |
| input-otp | 1.4.2 |
| lucide-react | 0.487.0 |
| motion | 12.23.24 |
| next-themes | 0.4.6 |
| react-day-picker | 9.14.0 |
| react-dnd | 16.0.1 |
| react-dnd-html5-backend | 16.0.1 |
| react-hook-form | 7.55.0 |
| react-resizable-panels | 2.1.7 |
| react-responsive-masonry | 2.7.1 |
| react-router | 8.3.0 |
| react-slick | 0.31.0 |
| recharts | 2.15.2 |
| sonner | 2.0.3 |
| tailwind-merge | 3.2.0 |
| tw-animate-css | 1.3.8 |
| vaul | 1.1.2 |

## npm Dev Dependencies

| Package | Version |
|---|---:|
| @tailwindcss/vite | 4.3.3 |
| @vitejs/plugin-react | 4.7.0 |
| tailwindcss | 4.3.3 |
| vite | 6.4.3 |

## Runtime Asset Boundary

The current release declares `agentops-pixel-office-code-rendered-v1` version
`1.0.0` in `assets/pixel-office/asset-manifest.json`. It consists of committed
React/TypeScript geometry, palettes, materials, characters, effects, and HUD
sources under `PROJECT_OWNED`; it contains zero third-party assets and no bitmap,
sprite, or tile payload. Future exported art must add source, creator, license,
and export hash before public or commercial use.
