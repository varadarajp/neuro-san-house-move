# House Move Agent: demo

A walk-through of the House Move network and app, from a live run against the real LLM.

## Video

[`house_move_demo.mp4`](house_move_demo.mp4) (about 3.5 minutes, with subtitles; captions also in
[`house_move_demo.srt`](house_move_demo.srt), narration script in [`narration.txt`](narration.txt)). It covers:

- the problem: a mortgage completion means telling 15 to 30 organisations, some of them legal duties;
- signing in to the House Move app and taking the signed session into nsflow;
- the agent graph in nsflow working through discovery, the dated plan and the drafts, with the hand-offs shown
  in Internal Chat (the live agent turns are sped up, and labelled with the factor);
- the app: the whole journey with the current step marked, approving the letters, and sending the pre-filled packs;
- how it is evaluated against ground truth, with the data leak the evaluations caught.

## Screenshots

Key steps of the app, in [`screenshots/`](screenshots):

| File | Step |
|---|---|
| `0-app-highlights.png` | The three key screens together |
| `1-sign-in.png` | Sign in |
| `2-move-overview.png` | The Move tab: progress, journey and next step |
| `3-your-journey.png` | The journey overview expanded |
| `4-approve-all.png` | Approve the letters |
| `5-send-your-own-pack.png` | A pre-filled pack the customer sends themselves |
| `6-progress-and-activity.png` | Progress, plan and recent activity |

Synthetic data only. See [`docs/examples/hackathon/house_move.md`](../docs/examples/hackathon/house_move.md) for the
full write-up and how to run it.
