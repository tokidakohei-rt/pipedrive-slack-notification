const axios = require('axios');
const qs = require('qs');
const fs = require('fs');
const path = require('path');
const YAML = require('yaml');

// Environment variables
const PIPEDRIVE_API_TOKEN = process.env.PIPEDRIVE_API_TOKEN;
const SLACK_BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const PIPELINE_ID = 32;
const AGENT_READY_STAGE_NAME = (process.env.AGENT_READY_STAGE_NAME || 'agent調整完了').trim();
const SLACK_THREAD_TS_FIELD_KEY = process.env.SLACK_THREAD_TS_FIELD_KEY;
const OWNER_SLACK_MAP_PATH = (process.env.OWNER_SLACK_MAP_PATH && path.resolve(process.env.OWNER_SLACK_MAP_PATH))
    || path.resolve(__dirname, '..', 'config', 'owner_slack_map.yaml');
const AGENT_FIXED_MENTIONS = (process.env.AGENT_FIXED_MENTIONS || 'U07PC1CSXH8,U03HP6CM1FB')
    .split(',')
    .map(id => id.trim())
    .filter(Boolean);
const HANDOVER_DATE_FIELD_KEY = process.env.HANDOVER_DATE_FIELD_KEY || 'b459bec642f11294904272a4fe6273d3591b9566';
const COUPON_SPREADSHEET_URL = process.env.COUPON_SPREADSHEET_URL
    || 'https://docs.google.com/spreadsheets/d/1kNxs6ibI6dDCwEGZv86EFNN5IZdfJNZn3QO9H-RK3Hs/edit?gid=387773158#gid=387773158';
const EARLY_NOTIFY_STAGE_NAMES = (process.env.EARLY_NOTIFY_STAGE_NAMES || '商談セット,Chat導入検討')
    .split(',')
    .map(stage => stage.trim())
    .filter(Boolean);
const CHAT_APPROVAL_STAGE_NAME = (process.env.CHAT_APPROVAL_STAGE_NAME || 'Chat導入内諾').trim();

let ownerSlackMapCache = null;

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
        if (isPipedriveWebhookPayload(body)) {
            console.log(`[${Date.now() - start}ms] Pipedrive webhook received.`);
            debugLogPipedrivePayload(body);
            await handlePipedriveWebhook(body);
            console.log(`[${Date.now() - start}ms] Pipedrive webhook handled.`);
            return res.status(200).send('ok');
        } else if (body.command === '/pipedrive-move') {
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
        debugLogUnexpectedBody(body);
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
    if (!stages.length) {
        const error = new Error('Pipedriveのステージが取得できません。パイプラインIDやAPI Tokenを確認してください。');
        error.exposeToSlack = true;
        throw error;
    }

    const dealPlaceholderOption = {
        text: {
            type: 'plain_text',
            text: '先にステージを選択してください'
        },
        value: 'placeholder'
    };

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
                dispatch_action: true,
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
                    options: [dealPlaceholderOption],
                    initial_option: dealPlaceholderOption
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
                dispatch_action: true,
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
    const deal = await fetchDeal(dealId);
    const stage = await fetchStage(targetStageId);

    // Notify User (optional, or just close modal)
    // To send a message, we need chat.postMessage
    await postSlackMessage(`<@${userId}> が "${deal?.title || `Deal ${dealId}`}" をステージ "${stage?.name || `Stage ${targetStageId}`}" に移動しました。`);
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

async function fetchDeal(dealId) {
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    try {
        const res = await axios.get(`https://api.pipedrive.com/v1/deals/${dealId}?api_token=${PIPEDRIVE_API_TOKEN}`);
        return res.data.data || null;
    } catch (e) {
        console.error('Fetch Deal Error', e);
        return null;
    }
}

async function fetchStage(stageId) {
    requireEnv('PIPEDRIVE_API_TOKEN', PIPEDRIVE_API_TOKEN);

    try {
        const res = await axios.get(`https://api.pipedrive.com/v1/stages/${stageId}?api_token=${PIPEDRIVE_API_TOKEN}`);
        return res.data.data || null;
    } catch (e) {
        console.error('Fetch Stage Error', e);
        return null;
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

    return value;
}

function logSlackResponse(apiName, data) {
    console.error(`[Slack API] ${apiName} raw:`, JSON.stringify(data, null, 2));

    if (!data) {
        console.error(`[Slack API] ${apiName} からレスポンスがありません`);
        return;
    }

    if (!data.ok) {
        const detail = data.response_metadata?.messages || data.errors || data;
        console.error(`[Slack API] ${apiName} error payload:`, JSON.stringify(detail));
        const error = new Error(`Slack API ${apiName} エラー: ${data.error || 'unknown_error'}`);
        error.exposeToSlack = true;
        throw error;
    }

    console.log(`[Slack API] ${apiName} response:`, JSON.stringify(data));
}

// --- Pipedrive Webhook Helpers ---

function isPipedriveWebhookPayload(body) {
    if (!body || typeof body !== 'object') {
        return false;
    }

    const meta = body.meta;
    if (meta) {
        if (typeof meta.object === 'string' && meta.object.toLowerCase() === 'deal') {
            return true;
        }
        if (typeof meta.entity === 'string' && meta.entity.toLowerCase() === 'deal') {
            return true;
        }
        if (typeof meta.type === 'string' && meta.type.toLowerCase() === 'deal') {
            return true;
        }
    }

    if (typeof body.object === 'string' && body.object.toLowerCase() === 'deal') {
        return true;
    }

    const eventName = (body.event || body.event_type || '').toString().toLowerCase();
    if (eventName.startsWith('deal.')) {
        return true;
    }

    if (body.current && typeof body.current === 'object') {
        return true;
    }

    if (body.data && typeof body.data === 'object') {
        return true;
    }

    return false;
}

async function handlePipedriveWebhook(body) {
    const event = classifyPipedriveEvent(body);

    if (!event) {
        console.log('[Pipedrive] No actionable event detected.');
        return;
    }

    console.log(`[Pipedrive] Event detected: ${event.type}`);
    if (event.type === 'deal_created') {
        await notifyDealCreated(event.deal);
    } else if (event.type === 'deal_stage_changed') {
        await notifyDealStageChanged(event.deal);
    }
}

function classifyPipedriveEvent(body) {
    const action = inferDealAction(body);
    const { current, previous } = extractDealPayload(body);

    if (!current) {
        return null;
    }

    if (action === 'added' || (!previous && action !== 'updated')) {
        return {
            type: 'deal_created',
            deal: current
        };
    }

    if ((action === 'updated' || previous) && previous) {
        const currentStageId = current.stage_id;
        const previousStageId = previous.stage_id;

        if (typeof currentStageId !== 'undefined'
            && typeof previousStageId !== 'undefined'
            && currentStageId !== previousStageId) {
            return {
                type: 'deal_stage_changed',
                deal: current,
                previous
            };
        }
    }

    return null;
}

function debugLogPipedrivePayload(body) {
    try {
        const masked = JSON.stringify(body, null, 2);
        console.log('[Pipedrive] Raw payload:', masked);
    } catch (error) {
        console.warn('[Pipedrive] Failed to stringify payload for debug:', error.message);
    }
}

function debugLogUnexpectedBody(body) {
    try {
        const masked = JSON.stringify(body, null, 2);
        console.log('[Debug] Unhandled body content:', masked);
    } catch (error) {
        console.warn('[Debug] Failed to stringify unhandled body:', error.message);
    }
}

function inferDealAction(body) {
    const metaAction = body?.meta?.action;
    if (metaAction) {
        if (metaAction === 'change') {
            return 'updated';
        }
        return metaAction;
    }

    const event = (body?.event || body?.event_type || '').toString().toLowerCase();
    if (event.includes('added') || event.includes('created')) {
        return 'added';
    }
    if (event.includes('updated') || event.includes('changed')) {
        return 'updated';
    }

    return null;
}

function extractDealPayload(body) {
    if (!body || typeof body !== 'object') {
        return { current: null, previous: null };
    }

    let current = body.current || null;
    let previous = body.previous || null;

    if (!current && body.data && typeof body.data === 'object') {
        current = body.data.current || body.data.deal || body.data;
        if (!previous && body.data.previous) {
            previous = body.data.previous;
        }
    }

    if (!current && body.deal) {
        current = body.deal;
    }

    return { current, previous };
}

async function getThreadTsForDeal(deal) {
    if (!SLACK_THREAD_TS_FIELD_KEY || !deal) {
        return null;
    }

    const inPayload = extractThreadTsFromDeal(deal);
    if (inPayload) {
        console.log(`[Slack] Found thread_ts=${inPayload} in webhook payload (deal ${deal.id})`);
        return inPayload;
    }

    const freshDeal = await fetchDeal(deal.id);
    const fetched = extractThreadTsFromDeal(freshDeal);

    if (fetched) {
        console.log(`[Slack] Found thread_ts=${fetched} from API (deal ${deal.id})`);
    } else {
        console.log(`[Slack] thread_ts not found for deal ${deal.id}`);
    }

    return fetched;
}

function extractThreadTsFromDeal(deal) {
    if (!deal || !SLACK_THREAD_TS_FIELD_KEY) {
        return null;
    }

    const directValue = deal[SLACK_THREAD_TS_FIELD_KEY];
    const customValue = deal.custom_fields?.[SLACK_THREAD_TS_FIELD_KEY];
    const nestedValue = customValue?.value;

    return normalizeFieldValue(directValue)
        || normalizeFieldValue(customValue)
        || normalizeFieldValue(nestedValue);
}

function normalizeFieldValue(value) {
    if (!value) {
        return null;
    }

    if (typeof value === 'string') {
        return value;
    }

    if (typeof value === 'object' && typeof value.value === 'string') {
        return value.value;
    }

    return null;
}

async function saveDealThreadTs(dealId, threadTs) {
    if (!SLACK_THREAD_TS_FIELD_KEY || !dealId || !threadTs) {
        return;
    }

    const payload = {
        [SLACK_THREAD_TS_FIELD_KEY]: threadTs
    };

    try {
        await axios.put(`https://api.pipedrive.com/v1/deals/${dealId}?api_token=${PIPEDRIVE_API_TOKEN}`, payload);
        console.log(`[Pipedrive] Saved thread_ts=${threadTs} to deal ${dealId}`);
    } catch (error) {
        console.error('[Pipedrive] Failed to save thread_ts:', error.message);
    }
}

async function notifyDealCreated(deal) {
    if (!deal) {
        return;
    }

    const title = deal.title || `Deal ${deal.id || '不明'}`;
    const ownerMention = formatOwnerMention(deal.owner_id);
    const stageName = await resolveStageName(deal);
    const stageLine = stageName ? `現在のステージ：${stageName}` : '現在のステージ：不明';
    const handoverDateLine = formatHandoverDate(deal);
    const fixedMentions = formatAgentFixedMentions();
    const footerLine = buildCreationFooter(stageName, fixedMentions);
    const textLines = [
        ':sparkles: 新しいカードが追加されました！',
        `企業名: ${title}`,
        stageLine,
        handoverDateLine,
        `担当: ${ownerMention}`
    ];

    if (footerLine) {
        textLines.push('', footerLine);
    }

    const slackResponse = await postSlackMessage(textLines.join('\n'));
    if (slackResponse?.ts) {
        await saveDealThreadTs(deal.id, slackResponse.ts);
    }
}

async function notifyDealStageChanged(deal) {
    if (!deal) {
        return;
    }

    const stageName = await resolveStageName(deal);

    if (!stageName) {
        console.warn('[Pipedrive] Stage name is missing; skip notification.');
        return;
    }

    if (stageName === AGENT_READY_STAGE_NAME) {
        const ownerMention = formatOwnerMention(deal.owner_id);
        const title = deal.title || `Deal ${deal.id || '不明'}`;
        const couponLine = COUPON_SPREADSHEET_URL
            ? `以下のスプレッドシートからクーポンコードを取得して、先方にご連絡お願いします！\n${COUPON_SPREADSHEET_URL}`
            : 'クーポンコードを取得して、先方にご連絡お願いします！';
        const textLines = [
            `${ownerMention ? `${ownerMention} ` : ''}${title}さんのagentが制度改善まで完了し、指定のメールアドレスに招待URLが送信されました！👏`,
            '',
            couponLine
        ];

        await postStageChangeMessage(deal, textLines);
        return;
    }

    if (stageName === CHAT_APPROVAL_STAGE_NAME) {
        const title = deal.title || `Deal ${deal.id || '不明'}`;
        const fixedMentions = formatAgentFixedMentions();
        const mentionLine = fixedMentions
            ? `${fixedMentions} はagentの準備を始めてください！`
            : 'agentの準備を始めてください！';
        const textLines = [
            `${title}さんがChatの導入を内諾しました！🎉`,
            '',
            mentionLine
        ];

        await postStageChangeMessage(deal, textLines);
        return;
    }

    console.log(`[Pipedrive] Stage "${stageName}" does not require notification; skip.`);
}

async function postSlackMessage(text, options = {}) {
    requireEnv('SLACK_BOT_TOKEN', SLACK_BOT_TOKEN);
    const channel = options.channel || requireEnv('SLACK_NOTIFY_CHANNEL', process.env.SLACK_NOTIFY_CHANNEL);
    const payload = { channel, text };

    if (options.threadTs) {
        payload.thread_ts = options.threadTs;
    }

    const slackRes = await axios.post('https://slack.com/api/chat.postMessage', payload, {
        headers: { Authorization: `Bearer ${SLACK_BOT_TOKEN}` }
    });

    logSlackResponse('chat.postMessage', slackRes.data);
    return slackRes.data;
}

function formatOwnerMention(ownerId) {
    if (typeof ownerId === 'undefined' || ownerId === null) {
        return '担当者未設定';
    }

    const slackId = getOwnerSlackId(ownerId);
    if (slackId) {
        return `<@${slackId}>`;
    }

    return `owner_id ${ownerId}`;
}

function getOwnerSlackId(ownerId) {
    const map = loadOwnerSlackMap();
    return map[String(ownerId)] || null;
}

function loadOwnerSlackMap() {
    if (ownerSlackMapCache !== null) {
        return ownerSlackMapCache;
    }

    try {
        const raw = fs.readFileSync(OWNER_SLACK_MAP_PATH, 'utf-8');
        const ext = path.extname(OWNER_SLACK_MAP_PATH).toLowerCase();

        if (ext === '.yaml' || ext === '.yml') {
            ownerSlackMapCache = YAML.parse(raw) || {};
        } else {
            ownerSlackMapCache = JSON.parse(raw);
        }
    } catch (error) {
        console.warn(`[Pipedrive] Failed to load owner_slack_map: ${error.message}`);
        ownerSlackMapCache = {};
    }

    return ownerSlackMapCache;
}

async function resolveStageName(deal) {
    if (!deal) {
        return '';
    }

    const stageNameFromPayload = (deal.stage_name || '').trim();
    if (stageNameFromPayload) {
        return stageNameFromPayload;
    }

    if (!deal.stage_id) {
        return '';
    }

    const stage = await fetchStage(deal.stage_id);
    return stage?.name?.trim() || '';
}

function formatAgentFixedMentions() {
    if (!AGENT_FIXED_MENTIONS.length) {
        return '';
    }

    return AGENT_FIXED_MENTIONS.map(id => `<@${id}>`).join(' ');
}

function formatHandoverDate(deal) {
    if (!HANDOVER_DATE_FIELD_KEY || !deal) {
        return '引き渡し希望日：未設定';
    }

    const value = extractCustomFieldValue(deal, HANDOVER_DATE_FIELD_KEY);
    if (!value) {
        return '引き渡し希望日：未設定';
    }

    return `引き渡し希望日：${value}`;
}

function extractCustomFieldValue(deal, fieldKey) {
    if (!deal || !fieldKey) {
        return null;
    }

    const directValue = deal[fieldKey];
    const customValue = deal.custom_fields?.[fieldKey];
    const nestedValue = customValue?.value;

    return normalizeFieldValue(directValue)
        || normalizeFieldValue(customValue)
        || normalizeFieldValue(nestedValue);
}

function buildCreationFooter(stageName, fixedMentions) {
    if (stageName && EARLY_NOTIFY_STAGE_NAMES.includes(stageName)) {
        return fixedMentions ? `cc: ${fixedMentions}` : '';
    }

    if (fixedMentions) {
        return `${fixedMentions} はagentの準備を始めてください！`;
    }

    return 'agentの準備を始めてください！';
}

async function postStageChangeMessage(deal, textLines) {
    const threadTs = await getThreadTsForDeal(deal);
    const slackResponse = await postSlackMessage(textLines.join('\n'), { threadTs });

    if (!threadTs && slackResponse?.ts) {
        await saveDealThreadTs(deal.id, slackResponse.ts);
    }
}
