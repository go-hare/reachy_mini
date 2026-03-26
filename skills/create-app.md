# Skill: Create App

## When to Use

- User wants to create a new Reachy Mini application
- User asks how to structure an app

## Important: Always Python Apps

**Always create Python apps using the app assistant.** Python apps are:
- The standard packaged app format in this repository
- Suitable when you want a standalone `main.py` app with optional `static/` web UI
- Compatible with the existing Reachy Mini app management flow

JS-only apps are not yet supported as the standard packaged app path. If the user needs a web UI, create a Python app with a web frontend in `static/`.

## Quick Check

If an app folder already exists with `README.md` containing `reachy_mini_python_app` tag, the app structure is probably already set up. In doubt, double check.

---

## Procedure

### Step 1: Use the App Assistant

**CRITICAL: Never create app folders manually.** Always use the assistant - it handles boilerplate, metadata tags, entry points, and proper structure. Manual creation leads to subtle issues that are hard to debug.

**If the command fails for any reason:** Ask the user to run it manually in their terminal rather than attempting complex workarounds.

```bash
# Python app package - minimal blank app:
reachy-mini-app-assistant create <app_name> <path>

# Shared-runtime app project:
reachy-mini-agent create <app_name>
```

#### Which path to choose?

| Path | Use when |
|------|----------|
| **`reachy-mini-app-assistant create`** | You need a traditional Python app package with `main.py` and optional `static/` web UI. |
| **`reachy-mini-agent create`** | You are creating an app project under `profiles/<app_name>/` that runs on the shared resident runtime. |

**IMPORTANT: Both `app_name` AND `path` are required for non-interactive mode.** If either is omitted, the command will prompt interactively (which fails in non-TTY environments like Claude Code).

Example:
```bash
reachy-mini-app-assistant create my_app_name .
reachy-mini-agent create my_app
```

### Step 2: Understand the Generated Structure

Traditional Python app packages look like this:

```text
my_app/
├── index.html              # Landing page
├── style.css               # Landing page styles
├── pyproject.toml          # Package config (includes reachy_mini tag)
├── README.md               # Must contain reachy_mini tag in YAML frontmatter
├── .gitignore
└── my_app/
    ├── __init__.py
    ├── main.py             # Your app code (run method)
    └── static/             # Optional web UI
        ├── index.html
        ├── style.css
        └── main.js
```

Shared-runtime app projects look like this:

```text
profiles/<app_name>/
├── README.md
├── pyproject.toml
├── .gitignore
├── index.html
├── style.css
├── <app_name>/
│   ├── __init__.py
│   ├── main.py
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── main.js
└── profiles/
    ├── AGENTS.md
    ├── USER.md
    ├── SOUL.md
    ├── TOOLS.md
    ├── FRONT.md
    ├── config.jsonl
    ├── memory/
    ├── skills/
    ├── session/
    ├── tools/
    └── prompts/
```

### Step 3: Development Workflow

1. **Create the Python app scaffold** with `reachy-mini-app-assistant create`, or create the shared-runtime app project with `reachy-mini-agent create`
2. **Fill in the generated files** for your app project
3. **Develop iteratively** using standard git: `git add`, `git commit`, `git push`

The app assistant no longer owns a `check` or `publish` flow.

### Step 4: Before Writing Code

Create a `plan.md` file in the app directory with:
- Your understanding of what the user wants
- Technical approach (components, patterns)
- Questions that need clarification

Wait for user to answer questions before implementing.

---

## Full Tutorial

For detailed guide with screenshots: https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps

---

## Common Patterns to Consider

When planning the app, consider which patterns apply:

| Pattern | When to use | Reference app |
|---------|-------------|---------------|
| Web UI | User needs visual interface | Most apps have optional static/ folder |
| No-GUI (antenna trigger) | Simple apps, kiosk mode | `reachy_mini_simon` |
| Control loop | Real-time reactivity needed | `reachy_mini_conversation_app/moves.py` |
| Head as controller | Games, recording | `fire_nation_attacked`, `marionette` |
| LLM integration | AI-powered behavior | `reachy_mini_conversation_app` |

---

## Creating a Beautiful Landing Page (index.html)

The root `index.html` is the landing page shown for packaged apps and can also serve as the top-level landing page for a shared-runtime app project. A well-designed landing page makes your app look professional and helps users understand what it does.

**Reference template:** Use the [Marionette app](https://huggingface.co/spaces/RemiFabre/marionette) as a template for the structure and styling.

### Structure

A good landing page has three sections:

1. **Hero Section** - Video/image + title + description
2. **Technical Section** - "How it works" steps + features
3. **Footer** - Resources, links, social media

### Key Elements

```text
index.html
├── Hero Section
│   ├── Demo video (autoplay, loop, muted)
│   ├── App emoji + title
│   ├── Tags (categories)
│   └── Short description (1-2 sentences)
│
├── Technical Section
│   ├── "How it works" (numbered steps)
│   └── "Features" or additional info
│
└── Footer
    ├── Resources (docs, troubleshooting)
    ├── Reachy Mini Apps links
    └── Social media icons
```

### Assets

Put demo videos/images in `<app_name>/assets/`:

```text
my_app/
├── index.html              # References my_app/assets/demo.mp4
├── my_app/
│   ├── assets/
│   │   └── demo.mp4        # Demo video
│   ├── main.py
│   └── static/             # Web UI (if any)
```

Video tag example:
```html
<video autoplay loop muted playsinline>
  <source src="my_app/assets/demo.mp4" type="video/mp4" />
</video>
```

### Styling

Use the Marionette CSS as a starting point:
- Inter font from Google Fonts
- CSS variables for colors (`--primary: #FF9900` for Pollen orange)
- Responsive grid layout (`grid-template-columns: 1fr 1fr` on desktop)
- Numbered steps with orange circles
- Footer with social media SVG icons

### Quick Checklist

- [ ] Hero with video/image showing the app in action
- [ ] Clear title and short tagline
- [ ] Tags describing the app's purpose
- [ ] "How it works" numbered steps (4 steps max)
- [ ] Footer with standard Pollen/Reachy links
- [ ] Responsive design (works on mobile)
- [ ] All CSS inline in `<style>` tag (no external stylesheet needed)
