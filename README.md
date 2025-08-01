# Use Any Poe.com Bot in the Qwen Code CLI

This project provides a simple API bridge built on [Modal](https://modal.com) that allows you to power the official [Qwen Code CLI](https://github.com/QwenLM/Qwen-Code) with your own Poe.com account.

This gives you the best of both worlds: the powerful, terminal-native interface of the Qwen Code CLI and the flexible, credit-based model access of your Poe account.

## Features

-   **Use Your Poe Credits:** Route all Qwen CLI requests through your Poe account.
-   **Dynamic Model Selection:** Switch between any of your available Poe bots on the fly directly from the command line.
-   **OpenAI-Compatible:** The bridge acts as a perfect impersonator of the OpenAI API, making it compatible with the Qwen CLI's configuration.
-   **Fully Streaming:** Responses stream back to your terminal in real-time for a smooth, interactive experience.
-   **Serverless:** Deploys instantly to Modal and only runs when you need it.

## How It Works

The solution works by creating a "translator" API that sits between the Qwen CLI and the Poe API.

`Qwen CLI` -> `Your Modal Bridge API` -> `Poe API`

The Qwen CLI thinks it's talking to an OpenAI server, but it's actually talking to your Modal app, which handles the logic of calling Poe and formatting the response.

## Prerequisites

Before you begin, you will need:
1.  A **Poe.com account** with API access.
2.  A **Modal account** ([sign up for free](https://modal.com/signup)).
3.  The **Qwen Code CLI** installed on your machine.
4.  **Python** and the **Modal CLI** installed locally (`pip install modal`).

## Setup Instructions

### Step 1: Clone This Repository

Clone this repository to your local machine:
```bash
git clone https://github.com/your-username/qwen-cli-poe-bridge.git
cd qwen-cli-poe-bridge
```

### Step 2: Create Your Poe API Secret

You need to securely store your Poe API key so the Modal app can access it.

1.  Get your API key from your [Poe account settings](https://poe.com/api_keys).
2.  In your terminal, create a Modal secret for it. **Replace `YOUR_POE_API_KEY_HERE` with your actual key.**
    ```bash
    modal secret create poe-api-caller-key-secret1 POE_CALLER_API_KEY1=YOUR_POE_API_KEY_HERE
    ```

### Step 3: Create Your Bridge Authentication Secret

This is a password *you* create to protect your Modal endpoint from being used by others.

1.  Generate a strong, random string. You can use a password manager or an online generator.
2.  In your terminal, create a second Modal secret for this token. **Replace `YOUR_CHOSEN_SECRET_TOKEN` with the password you just generated.**
    ```bash
    modal secret create modal-auth-token-secret MODAL_AUTH_TOKEN=YOUR_CHOSEN_SECRET_TOKEN
    ```

### Step 4: Deploy the Bridge to Modal

With the secrets in place, deploy the application.
```bash
modal deploy poe_qwen_bridge.py::app_modal
```
After a few seconds, the deployment will finish and Modal will give you a public URL. **Copy this URL.** It will look like `https://your-name--poe-qwen-bridge-openai-format-fastapi-app.modal.run`.

### Step 5: Configure the Qwen Code CLI

Now, tell the Qwen CLI to use your new Modal bridge. Run the `qwen` command. It will present you with a configuration screen. Fill it in as follows:

| Field      | What to Enter                                                              |
| :--------- | :------------------------------------------------------------------------- |
| **API Key**  | Paste the secret token you created in **Step 3**.                        |
| **Base URL** | Paste the Modal URL from **Step 4** and **add `/v1` to the end**.        |
| **Model**    | Enter the default Poe model you want to use, e.g., `Qwen-3-235B-0527-T`. |

**Example for Base URL:** `https://...modal.run/v1`

Press Enter to save. The configuration is complete!

## Usage

You can now use the `qwen` command directly from your terminal.

#### Using the Default Model

Simply type your prompt after the command.
```bash
qwen "Explain what a decorator is in Python."
```

#### Using a Different Poe Model

To override the default, start your prompt with `#@` followed by the exact model name from Poe.

```bash
qwen "#@Claude-3-Sonnet-32K Write a short story about a robot who discovers music."
```

```bash
qwen "#@Gemini-1.5-Pro I have a bug in my React component where the state isn't updating on click. What should I check?"
```

## Troubleshooting

-   **`[API Error: ... 404 status code]`**: Your **Base URL** in the Qwen CLI configuration is likely wrong. Make sure it's your full Modal URL and that it ends with `/v1`.
-   **`[API Error: ... 401 status code]`**: Your **API Key** in the Qwen CLI configuration is incorrect. Make sure it exactly matches the `MODAL_AUTH_TOKEN` secret you created in Step 3.
-   **`Error from Poe API: Cannot access private bots`**: The default model you've chosen is restricted. Try changing the `DEFAULT_POE_MODEL` in the Python script to a widely available bot like `Claude-3-Sonnet-32K`, re-deploy, and test again.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.