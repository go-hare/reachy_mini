# Skill: Create App

## When to Use

- User wants to create a new Reachy Mini app
- User asks how to structure a user app project
- User asks where `profiles/<name>/` or the inner `profiles/` files should live

## Important: One App Shape

**Always create the user app with `reachy-mini-agent create`.** In this repository:

- The user-created app is `profiles/<app_name>/`
- The inner `profiles/` directory is that app's editable profile file set
- The generated Python entry point hosts the resident runtime through `ReachyMiniApp`
- The web UI lives in `<app_name>/static/`

JS-only apps are not yet supported as the standard packaged app path.

## Quick Check

If an app folder already exists under `profiles/<app_name>/` with both `<app_name>/main.py` and `profiles/AGENTS.md`, the structure is probably already set up. In doubt, double check.

---

## Procedure

### Step 1: Use the Generator

**CRITICAL: Never create app folders manually.** Always use the generator so the entry point, static UI, and inner `profiles/` layout stay aligned.

**If the command fails for any reason:** Ask the user to run it manually in their terminal rather than attempting complex workarounds.

```bash
# Create profiles/<app_name>/ under the current apps root:
reachy-mini-agent create <app_name>
```

Example:

```bash
reachy-mini-agent create my_app
reachy-mini-agent agent my_app
```

### Step 2: Understand the Generated Structure

Generated app projects look like this:

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

Meaning:

- `profiles/<app_name>/` is the user app project root
- `<app_name>/main.py` is the generated Python host entry point
- `<app_name>/static/` is the app's web UI
- `profiles/` holds the editable app profile files that drive front, kernel, tools, memory, and session state

### Step 3: Development Workflow

1. **Create the app project** with `reachy-mini-agent create`
2. **Fill in the generated files** for your app project
3. **Run it locally** with `reachy-mini-agent agent <app_name>` while validating text behavior
4. **Install it into the daemon** when you want it to become the current background app

The old `check` and `publish` flow is no longer the main local AI workflow in this repository.

### Step 4: Before Writing Code

Create a `plan.md` file in the app directory with:
- Your understanding of what the user wants
- Technical approach (components, patterns)
- Questions that need clarification

Wait for user to answer questions before implementing.

---

## Optional Publishing Guide

For optional publishing guidance, the older app tutorial is still useful:
https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps

---

## Common Patterns to Consider

When planning the app, consider which patterns apply:

| Pattern | When to use | Reference app |
|---------|-------------|---------------|
| Web UI | User needs visual interface | Generated `<app_name>/static/` |
| No-GUI (antenna trigger) | Simple apps, kiosk mode | `reachy_mini_simon` |
| Control loop | Real-time reactivity needed | `reachy_mini_conversation_app/moves.py` |
| Head as controller | Games, recording | `fire_nation_attacked`, `marionette` |
| LLM integration | AI-powered behavior | `reachy_mini_conversation_app` |

---

## Creating a Beautiful Landing Page (index.html)

The root `index.html` is the landing page for the generated app project. A well-designed landing page makes your app look professional and helps users understand what it does.

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
profiles/<app_name>/
├── index.html
├── <app_name>/
│   ├── assets/
│   │   └── demo.mp4
│   ├── main.py
│   └── static/
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
