const axios = require('axios');
const qs = require('qs');

// Environment variables
const PIPEDRIVE_API_TOKEN = process.env.PIPEDRIVE_API_TOKEN;
const SLACK_BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const PIPELINE_ID = 32;

module.exports = async (req, res) => {
    const start = Date.now();
    console.log(`[${Date.now() - start}ms] Request received. Method: ${req.method}`);

    try {
        // Handle GET requests (Browser check)
        if (req.method === 'GET') {
            return res.status(200).send('Hello from Deal UI (GET request received)');
        }

        // 1. Parse the request body
        let body = await readRequestBody(req);
        body = body || {};
        console.log(`[${Date.now() - start}ms] Body type: ${typeof body}`);

        // If body is a string (raw), try to parse it
        if (typeof body === 'string') {
            try {
                // Check if it looks like JSON or Form Data
                if (body.startsWith('{')) {
                    body = JSON.parse(body);
                } else {
                    body = qs.parse(body);
                }
                console.log(`[${Date.now() - start}ms] Body parsed manually.`);
            } catch (e) {
                console.error('Body parse error:', e);
            }
        }

        let payload = null;

        if (body.payload) {
            try {
                payload = JSON.parse(body.payload);
                console.log(`[${Date.now() - start}ms] Payload parsed. Type: ${payload.type}`);
            } catch (e) {
                console.error('Error parsing payload:', e);
            }
        } else if (body.command) {
            console.log(`[${Date.now() - start}ms] Slash Command received: ${body.command}`);
        }

        // 2. Routing
        if (body.command === '/deal-ui') {
            // Slash Command -> Open Modal
            console.log(`[${Date.now() - start}ms] Opening Modal...`);
            await openModal(body.trigger_id);
            console.log(`[${Date.now() - start}ms] Modal Opened.`);
            return res.status(200).send();
        } else if (payload && payload.type === 'block_actions') {
            // Interactivity -> Update Modal (e.g. Stage selected)
            console.log(`[${Date.now() - start}ms] Handling Block Actions...`);
            await handleBlockActions(payload);
            console.log(`[${Date.now() - start}ms] Block Actions Handled.`);
            return res.status(200).send();
        } else if (payload && payload.type === 'view_submission') {
            // Submission -> Update Pipedrive
            console.log(`[${Date.now() - start}ms] Handling Submission...`);
            await handleViewSubmission(payload);
            console.log(`[${Date.now() - start}ms] Submission Handled.`);
            return res.status(200).send(); // Must return empty 200 to close modal, or update view
        }

        console.log(`[${Date.now() - start}ms] No matching route.`);
        return res.status(200).send('Hello from Deal UI (POST request received)');

    } catch (error) {
        console.error(`[${Date.now() - start}ms] Error:`, error);
        const exposeToSlack = error && error.exposeToSlack;
        const statusCode = exposeToSlack ? 200 : 500;
        const message = exposeToSlack ? `エラー: ${error.message}` : 'Internal Server Error';
        return res.status(statusCode).send(message);
    }
};

// --- Handlers ---

async function openModal(trigger_id) {
    requireEnv('SLACK_BOT_TOKEN', SLACK_BOT_TOKEN);
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    const stages = await fetchStages();

    const modal = {
        type: 'modal',
        callback_id: 'deal_ui_modal',
        title: {
            type: 'plain_text',
            text: 'Deal Stage Manager'
        },
        submit: {
            type: 'plain_text',
            text: '実行'
        },
        blocks: [
            {
                type: 'input',
                block_id: 'current_stage_block',
                label: {
                    type: 'plain_text',
                    text: '現在のステージ'
                },
                element: {
                    type: 'static_select',
                    action_id: 'current_stage_select',
                    placeholder: {
                        type: 'plain_text',
                        text: 'ステージを選択'
                    },
                    options: stages.map(s => ({
                        text: {
                            type: 'plain_text',
                            text: s.name
                        },
                        value: String(s.id)
                    }))
                }
            },
            {
                type: 'input',
                block_id: 'deal_block',
                label: {
                    type: 'plain_text',
                    text: '企業 (Deal)'
                },
                element: {
                    type: 'static_select',
                    action_id: 'deal_select',
                    placeholder: {
                        type: 'plain_text',
                        text: '先にステージを選択してください'
                    },
                    options: [] // Empty initially
                },
                optional: true // Optional initially to avoid validation error before selection? No, better to update it.
            },
            {
                type: 'input',
                block_id: 'target_stage_block',
                label: {
                    type: 'plain_text',
                    text: '移動先ステージ'
                },
                element: {
                    type: 'static_select',
                    action_id: 'target_stage_select',
                    placeholder: {
                        type: 'plain_text',
                        text: '移動先を選択'
                    },
                    options: stages.map(s => ({
                        text: {
                            type: 'plain_text',
                            text: s.name
                        },
                        value: String(s.id)
                    }))
                }
            }
        ]
    };

    const slackRes = await axios.post('https://slack.com/api/views.open', {
        trigger_id: trigger_id,
        view: modal
    }, {
        headers: { Authorization: `Bearer ${SLACK_BOT_TOKEN}` }
    });
    logSlackResponse('views.open', slackRes.data);
}

async function handleBlockActions(payload) {
    requireEnv('SLACK_BOT_TOKEN', SLACK_BOT_TOKEN);
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    const action = payload.actions[0];

    if (action.action_id === 'current_stage_select') {
        const stageId = action.selected_option.value;
        const deals = await fetchDeals(stageId);

        // Construct updated view
        // We need to update the 'deal_select' options
        // We can use views.update with the view_id

        // Re-fetch stages to keep the other dropdowns populated (or just reuse if static)
        // For simplicity, we'll just rebuild the blocks.
        const stages = await fetchStages(); // Optimization: Cache this or pass it through private_metadata

        const updatedBlocks = [
            {
                type: 'input',
                block_id: 'current_stage_block',
                label: { type: 'plain_text', text: '現在のステージ' },
                element: {
                    type: 'static_select',
                    action_id: 'current_stage_select',
                    placeholder: { type: 'plain_text', text: 'ステージを選択' },
                    options: stages.map(s => ({ text: { type: 'plain_text', text: s.name }, value: String(s.id) })),
                    initial_option: action.selected_option
                }
            },
            {
                type: 'input',
                block_id: 'deal_block',
                label: { type: 'plain_text', text: '企業 (Deal)' },
                element: {
                    type: 'static_select',
                    action_id: 'deal_select',
                    placeholder: { type: 'plain_text', text: '企業を選択' },
                    options: deals.length > 0 ? deals.map(d => ({
                        text: { type: 'plain_text', text: d.title },
                        value: String(d.id)
                    })) : [{ text: { type: 'plain_text', text: '案件なし' }, value: 'none' }]
                }
            },
            {
                type: 'input',
                block_id: 'target_stage_block',
                label: { type: 'plain_text', text: '移動先ステージ' },
                element: {
                    type: 'static_select',
                    action_id: 'target_stage_select',
                    placeholder: { type: 'plain_text', text: '移動先を選択' },
                    options: stages.map(s => ({ text: { type: 'plain_text', text: s.name }, value: String(s.id) }))
                }
            }
        ];

        const slackRes = await axios.post('https://slack.com/api/views.update', {
            view_id: payload.view.id,
            view: {
                type: 'modal',
                callback_id: 'deal_ui_modal',
                title: { type: 'plain_text', text: 'Deal Stage Manager' },
                submit: { type: 'plain_text', text: '実行' },
                blocks: updatedBlocks
            }
        }, {
            headers: { Authorization: `Bearer ${SLACK_BOT_TOKEN}` }
        });
        logSlackResponse('views.update', slackRes.data);
    }
}

async function handleViewSubmission(payload) {
    requireEnv('SLACK_BOT_TOKEN', SLACK_BOT_TOKEN);
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    const values = payload.view.state.values;
    const dealId = values.deal_block.deal_select.selected_option.value;
    const targetStageId = values.target_stage_block.target_stage_select.selected_option.value;
    const userId = payload.user.id;

    if (dealId === 'none') {
        return; // Do nothing or show error (requires returning response_action: errors)
    }

    // Update Pipedrive
    await updateDealStage(dealId, targetStageId);

    // Notify User (optional, or just close modal)
    // To send a message, we need chat.postMessage
    const slackRes = await axios.post('https://slack.com/api/chat.postMessage', {
        channel: userId, // DM the user
        text: `Deal ID ${dealId} をステージ ${targetStageId} に移動しました。`
    }, {
        headers: { Authorization: `Bearer ${SLACK_BOT_TOKEN}` }
    });
    logSlackResponse('chat.postMessage', slackRes.data);
}

// --- Pipedrive Helpers ---

async function fetchStages() {
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    try {
        const res = await axios.get(`https://api.pipedrive.com/v1/stages?pipeline_id=${PIPELINE_ID}&api_token=${PIPEDRIVE_API_TOKEN}`);
        return res.data.data || [];
    } catch (e) {
        console.error('Fetch Stages Error', e);
        return [];
    }
}

async function fetchDeals(stageId) {
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    try {
        const res = await axios.get(`https://api.pipedrive.com/v1/deals?pipeline_id=${PIPELINE_ID}&stage_id=${stageId}&status=open&api_token=${PIPEDRIVE_API_TOKEN}`);
        return res.data.data || [];
    } catch (e) {
        console.error('Fetch Deals Error', e);
        return [];
    }
}

async function updateDealStage(dealId, stageId) {
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    try {
        await axios.put(`https://api.pipedrive.com/v1/deals/${dealId}?api_token=${PIPEDRIVE_API_TOKEN}`, {
            stage_id: stageId
        });
    } catch (e) {
        console.error('Update Deal Error', e);
    }
}

async function readRequestBody(req) {
    if (!req) {
        return {};
    }

    if (typeof req.body === 'string' && req.body.length > 0) {
        return req.body;
    }

    if (req.body && typeof req.body === 'object' && Object.keys(req.body).length > 0) {
        return req.body;
    }

    if (req.readable) {
        const chunks = [];
        for await (const chunk of req) {
            chunks.push(chunk);
        }
        const raw = Buffer.concat(chunks).toString();
        return raw;
    }

    return {};
}

function requireEnv(name, value) {
    if (!value) {
        const error = new Error(`環境変数 ${name} が設定されていません`);
        error.exposeToSlack = true;
        throw error;
    }
}

function logSlackResponse(apiName, data) {
    if (!data) {
        console.error(`[Slack API] ${apiName} からレスポンスがありません`);
        return;
    }

    if (!data.ok) {
        const error = new Error(`Slack API ${apiName} エラー: ${data.error || 'unknown_error'}`);
        error.exposeToSlack = true;
        throw error;
    }

    console.log(`[Slack API] ${apiName} response:`, JSON.stringify(data));
}
