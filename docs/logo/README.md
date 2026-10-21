# The logo

The word **babyseat** over the sphere of field points, a few of them lit gold,
on deep ink: the opening frame of the film about the project (#368).

| File | Use |
|---|---|
| `logo-1920x1080.png` | banner, with the tagline "membrane · the memory inside" |
| `logo-1280x640.png` | the README header and GitHub's social preview card |
| `logo-square-1024.png` | avatars and icons |

Each PNG is drawn by the page beside it (`render-banner.html`,
`render-card.html`, `render-square.html`). The sphere is 420 evenly spaced
points drawn as decoration, not field data. To redo one, for example after
changing a page, use headless Chrome:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
      --hide-scrollbars --window-size=1280,640 --virtual-time-budget=8000 \
      --screenshot=docs/logo/logo-1280x640.png "file://$PWD/docs/logo/render-card.html"

The pages load Bricolage Grotesque and JetBrains Mono from Google Fonts, so
render them online.
