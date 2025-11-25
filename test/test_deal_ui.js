process.env.SLACK_BOT_TOKEN = 'test-slack-token';
process.env.PIPEDRIVE_API_TOKEN = 'test-pipedrive-token';
process.env.SLACK_NOTIFY_CHANNEL = 'C-test-channel';

const handler = require('../api/deal-ui');
const axios = require('axios');

// Mock Axios
jest = { fn: () => { } }; // Simple mock if we were using Jest, but we are running with node.
// We will monkey-patch axios for this simple test script.

const originalPost = axios.post;
const originalGet = axios.get;

const mockStages = {
    data: {
        data: [
            { id: 1, name: 'Stage 1' },
            { id: 2, name: 'Stage 2' }
        ]
    }
};

const mockDeals = {
    data: {
        data: [
            { id: 100, title: 'Deal A' },
            { id: 101, title: 'Deal B' }
        ]
    }
};

axios.get = async (url) => {
    console.log(`[Mock GET] ${url}`);
    if (url.includes('/stages')) return mockStages;
    if (url.includes('/deals')) return mockDeals;
    return { data: {} };
};

axios.post = async (url, data) => {
    console.log(`[Mock POST] ${url}`, JSON.stringify(data, null, 2));
    return { data: { ok: true } };
};

axios.put = async (url, data) => {
    console.log(`[Mock PUT] ${url}`, JSON.stringify(data, null, 2));
    return { data: { ok: true } };
};

// Mock Request/Response
const mockRes = {
    status: (code) => {
        console.log(`[Response Status] ${code}`);
        return mockRes;
    },
    send: (body) => {
        console.log(`[Response Body] ${body}`);
        return mockRes;
    }
};

async function runTests() {
    console.log('--- Test 1: Slash Command ---');
    await handler({
        body: { command: '/pipedrive-move', trigger_id: 'trigger_123' }
    }, mockRes);

    console.log('\n--- Test 2: Block Actions (Select Stage) ---');
    await handler({
        body: {
            payload: JSON.stringify({
                type: 'block_actions',
                view: { id: 'view_456' },
                actions: [{
                    action_id: 'current_stage_select',
                    selected_option: { value: '1' }
                }]
            })
        }
    }, mockRes);

    console.log('\n--- Test 3: View Submission (Update Stage) ---');
    await handler({
        body: {
            payload: JSON.stringify({
                type: 'view_submission',
                user: { id: 'U123' },
                view: {
                    state: {
                        values: {
                            deal_block: { deal_select: { selected_option: { value: '100' } } },
                            target_stage_block: { target_stage_select: { selected_option: { value: '2' } } }
                        }
                    }
                }
            })
        }
    }, mockRes);
}

runTests();
