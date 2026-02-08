
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });

export const askFarma = async (prompt: string, context?: any) => {
  const systemInstruction = `
    You are FARMA, an AI humanitarian expert for Nigeria.
    You assist the user in navigating the AEGIS Intelligence Pipeline.
    Current AEGIS State: ${JSON.stringify(context || {})}
    Keep responses professional, data-driven, and focused on humanitarian aid and food security.
    If the user asks about the pipeline, explain the 5 stages: Scan, Synthesis, Simulation, Report, and Marathon.
  `;

  try {
    const response = await ai.models.generateContent({
      model: "gemini-3-flash-preview",
      contents: prompt,
      config: {
        systemInstruction,
        temperature: 0.7,
      },
    });
    return response.text;
  } catch (error) {
    console.error("Gemini Error:", error);
    return "I'm sorry, I'm experiencing some connectivity issues. Please try again.";
  }
};
