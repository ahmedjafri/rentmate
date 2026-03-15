let BACKEND_URL = "http://localhost:8000"
/**
 * A placeholder for your rent management agent's API call.
 * This function now makes a POST request to a DevServer endpoint for suggestions.
 * @param {Array<Object>} conversationHistory The full conversation history as an an array of objects.
 * @param {string} [headerTitle] The title from the maintenance request header.
 * @param {string} [headerDescription] The description from the maintenance request header.
 * @returns {Promise<string>} A promise that resolves with the agent's suggested response.
 */
async function getAgentSuggestion(conversationHistory, headerTitle, headerDescription) {
    const url = `${BACKEND_URL}/suggest-reply`;

    // The data to be sent in the POST request body
    const requestData = {
        conversationHistory: conversationHistory,
        headerTitle: headerTitle,
        headerDescription: headerDescription
    };

    try {
        const response = await fetch(url, {
            method: 'POST', // Specify the method as POST
            headers: {
                'Content-Type': 'application/json', // Indicate that the request body is JSON
            },
            body: JSON.stringify(requestData) // Convert the JavaScript object to a JSON string
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();
        
        // The server's response should contain the suggestion.
        // The structure of 'result' will depend on your server's API.
        // Assuming the server returns a JSON object like: { "suggestion": "..." }
        if (result && result.suggestion) {
            return result.suggestion;
        } else {
            return "Could not get a valid suggestion from the server.";
        }

    } catch (error) {
        console.error('Error fetching agent suggestion:', error);
        return "An error occurred while trying to get a suggestion. Please try again later.";
    }
}
